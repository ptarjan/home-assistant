"""Support for Hikvision camera streams."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import CONF_RTSP_TRANSPORT, RTSP_TRANSPORTS
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.httpx_client import get_async_client

from . import HikvisionConfigEntry
from .const import CONF_RTSP_PORT, DEFAULT_RTSP_PORT, DOMAIN
from .helpers import HikvisionChannel, build_rtsp_url, build_snapshot_url

_LOGGER = logging.getLogger(__name__)

# Timeout for fetching snapshot images
GET_IMAGE_TIMEOUT = 10

# Use sub-stream (lower resolution) for snapshots to reduce load
SNAPSHOT_STREAM_TYPE = 1


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

    @property
    def _host(self) -> str:
        """Return the device host."""
        return self._entry.data[CONF_HOST]

    @property
    def _port(self) -> int:
        """Return the HTTP port."""
        return self._entry.data[CONF_PORT]

    @property
    def _rtsp_port(self) -> int:
        """Return the RTSP port."""
        return self._entry.data.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)

    @property
    def _username(self) -> str:
        """Return the username."""
        return self._entry.data[CONF_USERNAME]

    @property
    def _password(self) -> str:
        """Return the password."""
        return self._entry.data[CONF_PASSWORD]

    @property
    def _ssl(self) -> bool:
        """Return whether SSL is enabled."""
        return self._entry.data[CONF_SSL]

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return build_rtsp_url(
            host=self._host,
            port=self._rtsp_port,
            username=self._username,
            password=self._password,
            channel=self._channel.id,
            stream_type=1,  # Main stream for live view
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        snapshot_url = build_snapshot_url(
            host=self._host,
            port=self._port,
            channel=self._channel.id,
            ssl=self._ssl,
            stream_type=SNAPSHOT_STREAM_TYPE,
        )

        try:
            async_client = get_async_client(self.hass, verify_ssl=False)
            response = await async_client.get(
                snapshot_url,
                auth=httpx.DigestAuth(self._username, self._password),
                timeout=GET_IMAGE_TIMEOUT,
            )
            response.raise_for_status()
            self._last_image = response.content
            # Log recovery if we previously had errors
            if self._snapshot_error_logged:
                _LOGGER.info("Snapshot recovered for %s", self._channel.name)
                self._snapshot_error_logged = False
        except httpx.TimeoutException:
            if not self._snapshot_error_logged:
                _LOGGER.warning("Timeout getting snapshot from %s", self._channel.name)
                self._snapshot_error_logged = True
        except httpx.HTTPStatusError as err:
            if not self._snapshot_error_logged:
                _LOGGER.warning(
                    "HTTP error getting snapshot from %s: %s",
                    self._channel.name,
                    err.response.status_code,
                )
                self._snapshot_error_logged = True
        except httpx.RequestError as err:
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
