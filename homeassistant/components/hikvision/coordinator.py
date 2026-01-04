"""Data update coordinator for Hikvision integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .isapi import (
    AlarmServerInfo,
    CameraInfo,
    DeviceCapabilities,
    EventState,
    ISAPIClient,
    ISAPIConnectionError,
    ISAPIError,
    OutputPort,
    StorageDevice,
)

if TYPE_CHECKING:
    from . import HikvisionConfigEntry

_LOGGER = logging.getLogger(__name__)

# Update intervals
EVENTS_UPDATE_INTERVAL = timedelta(minutes=2)
SECONDARY_UPDATE_INTERVAL = timedelta(minutes=60)


@dataclass
class HikvisionCoordinatorData:
    """Data class for coordinator data."""

    # Device info
    device_serial: str = ""
    device_name: str = ""
    device_model: str = ""
    device_type: str = ""
    capabilities: DeviceCapabilities | None = None

    # Cameras and streams
    cameras: list[CameraInfo] = field(default_factory=list)

    # Event states
    event_states: dict[str, EventState] = field(default_factory=dict)

    # Output ports
    output_ports: list[OutputPort] = field(default_factory=list)
    output_states: dict[str, bool] = field(default_factory=dict)

    # Storage
    storage_devices: list[StorageDevice] = field(default_factory=list)

    # Alarm server
    alarm_server: AlarmServerInfo | None = None

    # Holiday mode
    holiday_mode_enabled: bool = False


class HikvisionDataUpdateCoordinator(DataUpdateCoordinator[HikvisionCoordinatorData]):
    """Coordinator for Hikvision device data updates."""

    config_entry: HikvisionConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        isapi_client: ISAPIClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Hikvision {config_entry.title}",
            update_interval=EVENTS_UPDATE_INTERVAL,
            config_entry=config_entry,
        )
        self.isapi_client = isapi_client
        self._initial_data_loaded = False

    async def _async_update_data(self) -> HikvisionCoordinatorData:
        """Fetch data from the device."""
        try:
            if not self._initial_data_loaded:
                return await self._fetch_initial_data()
            return await self._fetch_update_data()
        except ISAPIConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except ISAPIError as err:
            raise UpdateFailed(f"API error: {err}") from err

    async def _fetch_initial_data(self) -> HikvisionCoordinatorData:
        """Fetch initial device data."""
        data = HikvisionCoordinatorData()

        # Get device info
        data.device_serial = await self.isapi_client.get_device_serial()
        data.device_name = await self.isapi_client.get_device_name()
        data.device_model = await self.isapi_client.get_device_model()
        data.device_type = await self.isapi_client.get_device_type()

        # Get capabilities
        data.capabilities = await self.isapi_client.get_capabilities()

        # Get cameras
        data.cameras = await self.isapi_client.get_cameras()

        # Get output ports
        data.output_ports = await self.isapi_client.get_output_ports()
        for port in data.output_ports:
            data.output_states[port.id] = await self.isapi_client.get_output_state(
                port.id
            )

        # Get storage devices
        data.storage_devices = await self.isapi_client.get_storage_devices()

        # Get alarm server info
        data.alarm_server = await self.isapi_client.get_alarm_server_info()

        # Get holiday mode
        if data.capabilities and data.capabilities.support_holiday_mode:
            data.holiday_mode_enabled = (
                await self.isapi_client.get_holiday_mode_enabled()
            )

        # Get event states
        event_states = await self.isapi_client.get_event_states()
        data.event_states = {state.id: state for state in event_states}

        self._initial_data_loaded = True
        return data

    async def _fetch_update_data(self) -> HikvisionCoordinatorData:
        """Fetch updated device data."""
        data = HikvisionCoordinatorData(
            device_serial=self.data.device_serial,
            device_name=self.data.device_name,
            device_model=self.data.device_model,
            device_type=self.data.device_type,
            capabilities=self.data.capabilities,
            cameras=self.data.cameras,
            output_ports=self.data.output_ports,
        )

        # Update output states
        for port in data.output_ports:
            data.output_states[port.id] = await self.isapi_client.get_output_state(
                port.id
            )

        # Update storage devices
        data.storage_devices = await self.isapi_client.get_storage_devices()

        # Update alarm server info
        data.alarm_server = await self.isapi_client.get_alarm_server_info()

        # Update holiday mode
        if data.capabilities and data.capabilities.support_holiday_mode:
            data.holiday_mode_enabled = (
                await self.isapi_client.get_holiday_mode_enabled()
            )

        # Update event states
        event_states = await self.isapi_client.get_event_states()
        data.event_states = {state.id: state for state in event_states}

        return data


class HikvisionSecondaryCoordinator(DataUpdateCoordinator[HikvisionCoordinatorData]):
    """Secondary coordinator for less frequently updated data."""

    config_entry: HikvisionConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        primary_coordinator: HikvisionDataUpdateCoordinator,
    ) -> None:
        """Initialize the secondary coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Hikvision Secondary {config_entry.title}",
            update_interval=SECONDARY_UPDATE_INTERVAL,
            config_entry=config_entry,
        )
        self.primary_coordinator = primary_coordinator
        self.isapi_client = primary_coordinator.isapi_client

    async def _async_update_data(self) -> HikvisionCoordinatorData:
        """Fetch secondary data from the device."""
        try:
            # Get fresh storage data
            storage_devices = await self.isapi_client.get_storage_devices()

            # Get fresh alarm server info
            alarm_server = await self.isapi_client.get_alarm_server_info()

            # Return updated data based on primary
            if self.primary_coordinator.data:
                return HikvisionCoordinatorData(
                    device_serial=self.primary_coordinator.data.device_serial,
                    device_name=self.primary_coordinator.data.device_name,
                    device_model=self.primary_coordinator.data.device_model,
                    device_type=self.primary_coordinator.data.device_type,
                    capabilities=self.primary_coordinator.data.capabilities,
                    cameras=self.primary_coordinator.data.cameras,
                    output_ports=self.primary_coordinator.data.output_ports,
                    output_states=self.primary_coordinator.data.output_states,
                    event_states=self.primary_coordinator.data.event_states,
                    storage_devices=storage_devices,
                    alarm_server=alarm_server,
                    holiday_mode_enabled=self.primary_coordinator.data.holiday_mode_enabled,
                )

            return HikvisionCoordinatorData(
                storage_devices=storage_devices,
                alarm_server=alarm_server,
            )

        except ISAPIConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except ISAPIError as err:
            raise UpdateFailed(f"API error: {err}") from err
