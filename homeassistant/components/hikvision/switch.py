"""Switch platform for Hikvision integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionConfigEntry
from .coordinator import HikvisionDataUpdateCoordinator
from .entity import HikvisionEntity
from .isapi import ISAPIError

_LOGGER = logging.getLogger(__name__)


# Event type display names
EVENT_TYPE_NAMES: dict[str, str] = {
    "motionDetection": "Motion detection",
    "lineDetection": "Line crossing detection",
    "fieldDetection": "Intrusion detection",
    "regionEntrance": "Region entrance",
    "regionExiting": "Region exiting",
    "tamperDetection": "Tamper detection",
    "sceneChangeDetection": "Scene change detection",
    "PIR": "PIR",
    "faceDetection": "Face detection",
}


@dataclass(frozen=True, kw_only=True)
class HikvisionSwitchEntityDescription(SwitchEntityDescription):
    """Describe a Hikvision switch entity."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hikvision switches from a config entry."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data

    if data is None:
        return

    entities: list[SwitchEntity] = [
        HikvisionEventSwitch(
            coordinator=coordinator,
            entry=entry,
            event_id=event_id,
            event_type=event_state.type,
            channel=event_state.channel,
        )
        for event_id, event_state in data.event_states.items()
    ]

    # Add output port switches
    entities.extend(
        HikvisionOutputSwitch(
            coordinator=coordinator,
            entry=entry,
            port_id=port.id,
            port_name=port.name,
        )
        for port in data.output_ports
    )

    # Add holiday mode switch if supported
    if data.capabilities and data.capabilities.support_holiday_mode:
        entities.append(
            HikvisionHolidayModeSwitch(
                coordinator=coordinator,
                entry=entry,
            )
        )

    async_add_entities(entities)


class HikvisionEventSwitch(
    HikvisionEntity,
    CoordinatorEntity[HikvisionDataUpdateCoordinator],
    SwitchEntity,
):
    """Representation of a Hikvision event detection switch."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: HikvisionDataUpdateCoordinator,
        entry: HikvisionConfigEntry,
        event_id: str,
        event_type: str,
        channel: int,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry)
        self._event_id = event_id
        self._event_type = event_type
        self._channel = channel
        self._attr_unique_id = f"{entry.runtime_data.device_id}_event_{event_id}"
        self._attr_translation_key = "event_detection"

        # Set name based on event type and channel
        event_name = EVENT_TYPE_NAMES.get(event_type, event_type)
        if len(coordinator.data.cameras) > 1:
            self._attr_translation_placeholders = {
                "event_type": event_name,
                "channel": str(channel),
            }
        else:
            self._attr_name = event_name

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        if self.coordinator.data is None:
            return False
        event = self.coordinator.data.event_states.get(self._event_id)
        return event.enabled if event else False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            await self.coordinator.isapi_client.set_event_enabled(
                self._event_type, self._channel, True
            )
            await self.coordinator.async_request_refresh()
        except ISAPIError as err:
            _LOGGER.error("Failed to enable event detection: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            await self.coordinator.isapi_client.set_event_enabled(
                self._event_type, self._channel, False
            )
            await self.coordinator.async_request_refresh()
        except ISAPIError as err:
            _LOGGER.error("Failed to disable event detection: %s", err)


class HikvisionOutputSwitch(
    HikvisionEntity,
    CoordinatorEntity[HikvisionDataUpdateCoordinator],
    SwitchEntity,
):
    """Representation of a Hikvision output port switch."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: HikvisionDataUpdateCoordinator,
        entry: HikvisionConfigEntry,
        port_id: str,
        port_name: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry)
        self._port_id = port_id
        self._attr_unique_id = f"{entry.runtime_data.device_id}_output_{port_id}"
        self._attr_translation_key = "output_port"
        self._attr_translation_placeholders = {"port_name": port_name}

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        if self.coordinator.data is None:
            return False
        return self.coordinator.data.output_states.get(self._port_id, False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            await self.coordinator.isapi_client.set_output_state(self._port_id, True)
            await self.coordinator.async_request_refresh()
        except ISAPIError as err:
            _LOGGER.error("Failed to turn on output port: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            await self.coordinator.isapi_client.set_output_state(self._port_id, False)
            await self.coordinator.async_request_refresh()
        except ISAPIError as err:
            _LOGGER.error("Failed to turn off output port: %s", err)


class HikvisionHolidayModeSwitch(
    HikvisionEntity,
    CoordinatorEntity[HikvisionDataUpdateCoordinator],
    SwitchEntity,
):
    """Representation of a Hikvision holiday mode switch."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "holiday_mode"
    _attr_icon = "mdi:palm-tree"

    def __init__(
        self,
        coordinator: HikvisionDataUpdateCoordinator,
        entry: HikvisionConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.runtime_data.device_id}_holiday_mode"

    @property
    def is_on(self) -> bool:
        """Return true if holiday mode is enabled."""
        if self.coordinator.data is None:
            return False
        return self.coordinator.data.holiday_mode_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on holiday mode."""
        try:
            await self.coordinator.isapi_client.set_holiday_mode_enabled(True)
            await self.coordinator.async_request_refresh()
        except ISAPIError as err:
            _LOGGER.error("Failed to enable holiday mode: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off holiday mode."""
        try:
            await self.coordinator.isapi_client.set_holiday_mode_enabled(False)
            await self.coordinator.async_request_refresh()
        except ISAPIError as err:
            _LOGGER.error("Failed to disable holiday mode: %s", err)
