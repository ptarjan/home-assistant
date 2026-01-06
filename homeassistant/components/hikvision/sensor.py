"""Sensor platform for Hikvision integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HikvisionConfigEntry
from .coordinator import HikvisionCoordinatorData, HikvisionSecondaryCoordinator
from .entity import HikvisionEntity


@dataclass(frozen=True, kw_only=True)
class HikvisionStorageSensorDescription(SensorEntityDescription):
    """Describe a Hikvision storage sensor."""

    value_fn: Callable[[Any], str | int | None]
    extra_state_fn: Callable[[Any], dict[str, Any]] | None = None


STORAGE_SENSORS: tuple[HikvisionStorageSensorDescription, ...] = (
    HikvisionStorageSensorDescription(
        key="status",
        translation_key="storage_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda storage: storage.status,
        extra_state_fn=lambda storage: {
            "type": storage.type,
            "ip_address": storage.ip_address,
        },
    ),
    HikvisionStorageSensorDescription(
        key="capacity",
        translation_key="storage_capacity",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda storage: storage.capacity,
    ),
    HikvisionStorageSensorDescription(
        key="free_space",
        translation_key="storage_free_space",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda storage: storage.free_space,
    ),
)


@dataclass(frozen=True, kw_only=True)
class HikvisionAlarmServerSensorDescription(SensorEntityDescription):
    """Describe a Hikvision alarm server sensor."""

    value_fn: Callable[[HikvisionCoordinatorData], str | int | None]


ALARM_SERVER_SENSORS: tuple[HikvisionAlarmServerSensorDescription, ...] = (
    HikvisionAlarmServerSensorDescription(
        key="protocol",
        translation_key="alarm_server_protocol",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.alarm_server.protocol if data.alarm_server else None,
    ),
    HikvisionAlarmServerSensorDescription(
        key="address",
        translation_key="alarm_server_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.alarm_server.address if data.alarm_server else None,
    ),
    HikvisionAlarmServerSensorDescription(
        key="port",
        translation_key="alarm_server_port",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.alarm_server.port
            if data.alarm_server and data.alarm_server.port
            else None
        ),
    ),
    HikvisionAlarmServerSensorDescription(
        key="path",
        translation_key="alarm_server_path",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.alarm_server.path if data.alarm_server else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hikvision sensors from a config entry."""
    coordinator = entry.runtime_data.secondary_coordinator
    data = coordinator.data

    if data is None:
        return

    entities: list[SensorEntity] = [
        HikvisionStorageSensor(
            coordinator=coordinator,
            entry=entry,
            description=description,
            storage_id=storage.id,
            storage_name=storage.name,
        )
        for storage in data.storage_devices
        for description in STORAGE_SENSORS
    ]

    # Add alarm server sensors if supported
    if data.capabilities and data.capabilities.support_alarm_server:
        entities.extend(
            HikvisionAlarmServerSensor(
                coordinator=coordinator,
                entry=entry,
                description=description,
            )
            for description in ALARM_SERVER_SENSORS
        )

    async_add_entities(entities)


class HikvisionStorageSensor(HikvisionEntity, SensorEntity):
    """Representation of a Hikvision storage sensor."""

    entity_description: HikvisionStorageSensorDescription

    def __init__(
        self,
        coordinator: HikvisionSecondaryCoordinator,
        entry: HikvisionConfigEntry,
        description: HikvisionStorageSensorDescription,
        storage_id: str,
        storage_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._storage_id = storage_id
        self._storage_name = storage_name
        self._attr_unique_id = (
            f"{entry.runtime_data.device_id}_storage_{storage_id}_{description.key}"
        )
        self._attr_translation_placeholders = {"storage_name": storage_name}
        # Set explicit name since device_class may override translation
        name_map = {
            "status": f"{storage_name} status",
            "capacity": f"{storage_name} capacity",
            "free_space": f"{storage_name} free space",
        }
        if description.key in name_map:
            self._attr_name = name_map[description.key]

    @property
    def _storage(self) -> Any | None:
        """Get the storage device data."""
        if self.coordinator.data is None:
            return None
        for storage in self.coordinator.data.storage_devices:
            if storage.id == self._storage_id:
                return storage
        return None

    @property
    def native_value(self) -> str | int | None:
        """Return the sensor value."""
        if (storage := self._storage) is None:
            return None
        return self.entity_description.value_fn(storage)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if self.entity_description.extra_state_fn is None:
            return None
        if (storage := self._storage) is None:
            return None
        return self.entity_description.extra_state_fn(storage)


class HikvisionAlarmServerSensor(HikvisionEntity, SensorEntity):
    """Representation of a Hikvision alarm server sensor."""

    entity_description: HikvisionAlarmServerSensorDescription

    def __init__(
        self,
        coordinator: HikvisionSecondaryCoordinator,
        entry: HikvisionConfigEntry,
        description: HikvisionAlarmServerSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = (
            f"{entry.runtime_data.device_id}_alarm_server_{description.key}"
        )

    @property
    def native_value(self) -> str | int | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
