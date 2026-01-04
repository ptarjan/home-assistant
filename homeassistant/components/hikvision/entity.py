"""Base entity for Hikvision integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from . import HikvisionConfigEntry
    from .coordinator import (
        HikvisionDataUpdateCoordinator,
        HikvisionSecondaryCoordinator,
    )


class HikvisionEntity(
    CoordinatorEntity["HikvisionDataUpdateCoordinator | HikvisionSecondaryCoordinator"]
):
    """Base class for Hikvision entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HikvisionDataUpdateCoordinator | HikvisionSecondaryCoordinator,
        entry: HikvisionConfigEntry,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._entry = entry

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.runtime_data.device_id)},
            name=entry.runtime_data.device_name,
            manufacturer="Hikvision",
            model=entry.runtime_data.device_model or entry.runtime_data.device_type,
            sw_version=entry.runtime_data.firmware_version,
        )
