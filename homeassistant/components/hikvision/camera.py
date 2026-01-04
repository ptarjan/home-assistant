"""Camera platform for Hikvision integration."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
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
    """Set up Hikvision cameras from a config entry."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data

    if data is None:
        return

    entities: list[Camera] = [
        HikvisionCamera(
            coordinator=coordinator,
            entry=entry,
            camera_id=camera.id,
            camera_name=camera.name,
            stream_id=stream.id,
            stream_type=stream.type_id,
            is_main_stream=stream.type_id == 1,
        )
        for camera in data.cameras
        for stream in camera.streams
    ]

    async_add_entities(entities)


class HikvisionCamera(HikvisionEntity, Camera):
    """Representation of a Hikvision camera stream."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: HikvisionDataUpdateCoordinator,
        entry: HikvisionConfigEntry,
        camera_id: int,
        camera_name: str,
        stream_id: str,
        stream_type: int,
        is_main_stream: bool,
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator, entry)
        Camera.__init__(self)

        self._camera_id = camera_id
        self._stream_id = stream_id
        self._stream_type = stream_type
        self._attr_unique_id = f"{entry.runtime_data.device_id}_camera_{stream_id}"

        # Main stream uses camera name, sub streams get numbered names
        if is_main_stream:
            self._attr_name = camera_name
        else:
            self._attr_name = f"{camera_name} stream {stream_type}"
            # Sub streams are disabled by default
            self._attr_entity_registry_enabled_default = False

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        try:
            return await self.coordinator.isapi_client.get_snapshot(
                self._camera_id, width, height
            )
        except ISAPIError as err:
            _LOGGER.error("Failed to get camera image: %s", err)
            return None

    async def stream_source(self) -> str | None:
        """Return the stream source URL."""
        return self.coordinator.isapi_client.get_rtsp_url(
            self._camera_id, self._stream_type
        )
