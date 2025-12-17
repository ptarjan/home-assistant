"""The Hikvision integration."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from pyhik.hikvision import HikCamera

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
from homeassistant.exceptions import ConfigEntryNotReady

from .helpers import (
    HikvisionChannel,
    get_nvr_events,
    get_video_channels,
    inject_events_into_camera,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CAMERA]


@dataclass
class HikvisionData:
    """Data class for Hikvision runtime data."""

    camera: HikCamera
    device_id: str
    device_name: str
    device_type: str
    channels: list[HikvisionChannel] = field(default_factory=list)


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

    try:
        camera = await hass.async_add_executor_job(
            HikCamera, url, port, username, password
        )
    except Exception as err:
        raise ConfigEntryNotReady(f"Unable to connect to {host}") from err

    device_id = camera.get_id()
    if device_id is None:
        raise ConfigEntryNotReady(f"Unable to get device ID from {host}")

    device_name = camera.get_name or host
    device_type = camera.get_type or "Camera"

    # For NVRs, pyhik may not detect events with 'record' notification method
    # Fetch events separately and inject them into the camera object
    if device_type == "NVR" or not camera.current_event_states:
        nvr_events = await hass.async_add_executor_job(
            get_nvr_events, host, port, username, password, ssl
        )
        if nvr_events:
            await hass.async_add_executor_job(
                inject_events_into_camera, camera, nvr_events
            )

    # Discover video channels for camera entities
    channels = await hass.async_add_executor_job(
        get_video_channels, host, port, username, password, ssl
    )

    entry.runtime_data = HikvisionData(
        camera=camera,
        device_id=device_id,
        device_name=device_name,
        device_type=device_type,
        channels=channels,
    )

    # Start the event stream
    await hass.async_add_executor_job(camera.start_stream)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Stop the event stream
        await hass.async_add_executor_job(entry.runtime_data.camera.disconnect)

    return unload_ok
