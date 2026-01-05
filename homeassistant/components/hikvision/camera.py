"""Support for Hikvision camera streams."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import CONF_RTSP_TRANSPORT, RTSP_TRANSPORTS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HikvisionConfigEntry
from .const import DOMAIN
from .helpers import HikvisionChannel

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hikvision cameras from a config entry."""
    data = entry.runtime_data
    channels = data.channels

    if not channels:
        return

    async_add_entities(
        HikvisionCamera(entry=entry, channel=channel)
        for channel in channels
        if channel.enabled
    )


class HikvisionCamera(Camera):
    """Representation of a Hikvision camera stream."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        entry: HikvisionConfigEntry,
        channel: HikvisionChannel,
    ) -> None:
        """Initialize the camera."""
        super().__init__()
        self._entry = entry
        self._data = entry.runtime_data
        self._channel = channel

        # Build unique ID
        self._attr_unique_id = f"{self._data.device_id}_camera_{channel.id}"

        # Build entity name based on device type and channel count
        if self._data.device_type == "NVR" or len(self._data.channels) > 1:
            self._attr_name = channel.name
        else:
            # Single camera - use None to just use device name
            self._attr_name = None

        # Device info for device registry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._data.device_id)},
            name=self._data.device_name,
            manufacturer="Hikvision",
            model=self._data.device_type,
        )

        # Configure RTSP transport (TCP is more reliable)
        self.stream_options[CONF_RTSP_TRANSPORT] = entry.options.get(
            CONF_RTSP_TRANSPORT, next(iter(RTSP_TRANSPORTS))
        )

        # Cache for snapshot image
        self._last_image: bytes | None = None
        # Track error state to avoid log spam
        self._snapshot_error_logged: bool = False

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        # Use pyHik's get_stream_url method
        camera = self._data.camera
        return await self.hass.async_add_executor_job(
            camera.get_stream_url, self._channel.id, "rtsp", 1
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        camera = self._data.camera

        try:
            # Use pyHik's get_snapshot method (blocking, needs executor)
            image = await self.hass.async_add_executor_job(
                camera.get_snapshot, self._channel.id
            )
            if image:
                self._last_image = image
                # Log recovery if we previously had errors
                if self._snapshot_error_logged:
                    _LOGGER.info("Snapshot recovered for %s", self._channel.name)
                    self._snapshot_error_logged = False
        except Exception as err:  # noqa: BLE001
            if not self._snapshot_error_logged:
                _LOGGER.warning(
                    "Error getting snapshot from %s: %s", self._channel.name, err
                )
                self._snapshot_error_logged = True

        return self._last_image

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "channel_id": self._channel.id,
            "channel_name": self._channel.name,
        }
