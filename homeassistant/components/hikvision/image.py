"""Image platform for Hikvision integration."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HikvisionConfigEntry
from .coordinator import HikvisionDataUpdateCoordinator
from .entity import HikvisionEntity
from .isapi import ISAPIError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hikvision image entities from a config entry."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data

    if data is None:
        return

    entities: list[ImageEntity] = []

    for camera in data.cameras:
        # Only create image entity for main streams
        main_streams = [s for s in camera.streams if s.type_id == 1]
        if main_streams:
            entities.append(
                HikvisionSnapshot(
                    coordinator=coordinator,
                    entry=entry,
                    camera_id=camera.id,
                    camera_name=camera.name,
                )
            )

    async_add_entities(entities)


class HikvisionSnapshot(HikvisionEntity, ImageEntity):
    """Representation of a Hikvision camera snapshot."""

    def __init__(
        self,
        coordinator: HikvisionDataUpdateCoordinator,
        entry: HikvisionConfigEntry,
        camera_id: int,
        camera_name: str,
    ) -> None:
        """Initialize the image entity."""
        super().__init__(coordinator, entry)
        ImageEntity.__init__(self, coordinator.hass)

        self._camera_id = camera_id
        self._attr_unique_id = f"{entry.runtime_data.device_id}_snapshot_{camera_id}"
        self._attr_name = f"{camera_name} snapshot"
        self._cached_image: bytes | None = None

    async def async_image(self) -> bytes | None:
        """Return the camera snapshot."""
        try:
            image = await self.coordinator.isapi_client.get_snapshot(self._camera_id)
        except ISAPIError as err:
            _LOGGER.warning("Failed to get snapshot: %s", err)
            return self._cached_image
        else:
            self._cached_image = image
            self._attr_image_last_updated = datetime.now()
            return image
