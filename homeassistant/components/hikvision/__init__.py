"""The Hikvision integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from pyhik.constants import SENSOR_MAP
from pyhik.hikvision import HikCamera
import requests

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import HikvisionDataUpdateCoordinator, HikvisionSecondaryCoordinator
from .isapi import ISAPIAuthError, ISAPIClient, ISAPIConnectionError, ISAPIError
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class HikvisionData:
    """Data class for Hikvision runtime data."""

    camera: HikCamera
    device_id: str
    device_name: str
    device_type: str
    device_model: str
    firmware_version: str | None
    isapi_client: ISAPIClient
    coordinator: HikvisionDataUpdateCoordinator
    secondary_coordinator: HikvisionSecondaryCoordinator


type HikvisionConfigEntry = ConfigEntry[HikvisionData]


async def async_setup_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Set up Hikvision from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    ssl = entry.data[CONF_SSL]

    protocol = "https" if ssl else "http"
    url = f"{protocol}://{host}"

    # Create ISAPI client for extended functionality
    isapi_client = ISAPIClient(hass, host, port, username, password, ssl)

    # Validate connection using ISAPI
    try:
        device_info = await isapi_client.get_device_info()
        device_serial = device_info.get("serialNumber", "")
        device_name = device_info.get("deviceName", "") or host
        device_model = device_info.get("model", "")
        device_type = device_info.get("deviceType", "Camera")
        firmware_version = device_info.get("firmwareVersion")
    except ISAPIAuthError as err:
        raise ConfigEntryAuthFailed("Invalid credentials") from err
    except ISAPIConnectionError as err:
        raise ConfigEntryNotReady(f"Unable to connect to {host}") from err
    except ISAPIError as err:
        raise ConfigEntryNotReady(f"Error communicating with {host}: {err}") from err

    # Create pyhik camera for binary sensor event streaming
    try:
        camera = await hass.async_add_executor_job(
            HikCamera, url, port, username, password, ssl
        )
    except requests.exceptions.RequestException as err:
        raise ConfigEntryNotReady(f"Unable to connect to {host}") from err

    device_id = camera.get_id or device_serial
    if not device_id:
        raise ConfigEntryNotReady(f"Unable to get device ID from {host}")

    # Use device name from pyhik if available
    if camera.get_name:
        device_name = camera.get_name
    if camera.get_type:
        device_type = camera.get_type

    # Create coordinators
    coordinator = HikvisionDataUpdateCoordinator(hass, entry, isapi_client)
    secondary_coordinator = HikvisionSecondaryCoordinator(hass, entry, coordinator)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()
    await secondary_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = HikvisionData(
        camera=camera,
        device_id=device_id,
        device_name=device_name,
        device_type=device_type,
        device_model=device_model,
        firmware_version=firmware_version,
        isapi_client=isapi_client,
        coordinator=coordinator,
        secondary_coordinator=secondary_coordinator,
    )

    _LOGGER.debug(
        "Device %s (type=%s) initial event_states: %s",
        device_name,
        device_type,
        camera.current_event_states,
    )

    # For NVRs or devices with no detected events, try to fetch events from ISAPI
    # Use broader notification methods for NVRs since they often use 'record' etc.
    if device_type == "NVR" or not camera.current_event_states:
        nvr_notification_methods = {"center", "HTTP", "record", "email", "beep"}

        def fetch_and_inject_nvr_events() -> None:
            """Fetch and inject NVR events in a single executor job."""
            nvr_events = camera.get_event_triggers(nvr_notification_methods)
            _LOGGER.debug("NVR events fetched with extended methods: %s", nvr_events)
            if nvr_events:
                # Map raw event type names to friendly names using SENSOR_MAP
                mapped_events: dict[str, list[int]] = {}
                for event_type, channels in nvr_events.items():
                    friendly_name = SENSOR_MAP.get(event_type.lower(), event_type)
                    if friendly_name in mapped_events:
                        mapped_events[friendly_name].extend(channels)
                    else:
                        mapped_events[friendly_name] = list(channels)
                _LOGGER.debug("Mapped NVR events: %s", mapped_events)
                camera.inject_events(mapped_events)

        await hass.async_add_executor_job(fetch_and_inject_nvr_events)

    # Start the event stream for binary sensors
    await hass.async_add_executor_job(camera.start_stream)

    # Set up services if this is the first entry
    if len(hass.config_entries.async_entries(DOMAIN)) == 1:
        await async_setup_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Stop the event stream
        await hass.async_add_executor_job(entry.runtime_data.camera.disconnect)

        # Unload services if this is the last entry
        if len(hass.config_entries.async_entries(DOMAIN)) == 1:
            await async_unload_services(hass)

    return unload_ok
