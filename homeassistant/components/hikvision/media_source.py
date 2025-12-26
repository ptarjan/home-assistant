"""Expose Hikvision camera recordings as media sources."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from urllib.parse import quote_plus, unquote

from homeassistant.components.camera import DynamicStreamSettings
from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.components.stream import create_stream
from homeassistant.core import HomeAssistant

from . import HikvisionConfigEntry
from .const import DOMAIN
from .isapi import HikvisionISAPIClient

_LOGGER = logging.getLogger(__name__)


async def async_get_media_source(hass: HomeAssistant) -> HikvisionMediaSource:
    """Set up Hikvision media source."""
    return HikvisionMediaSource(hass)


class HikvisionMediaSource(MediaSource):
    """Provide Hikvision camera recordings as media sources."""

    name: str = "Hikvision"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize HikvisionMediaSource."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable URL."""
        if item.identifier is None:
            raise Unresolvable("No media item identifier provided")

        identifier = item.identifier.split("|")
        if identifier[0] != "FILE":
            raise Unresolvable(f"Unknown media item '{item.identifier}'")

        if len(identifier) < 6:
            raise Unresolvable(f"Invalid media identifier format: {item.identifier}")

        _, config_entry_id, track_id_str, start_time_str, end_time_str, playback_uri = (
            identifier[0],
            identifier[1],
            identifier[2],
            identifier[3],
            identifier[4],
            identifier[5] if len(identifier) > 5 else "",
        )

        entry = self._get_config_entry(config_entry_id)
        if entry is None:
            raise Unresolvable(f"Config entry {config_entry_id} not found")

        # Construct the RTSP URL for playback
        if playback_uri:
            # Decode the playback URI
            decoded_uri = unquote(playback_uri)

            # Add credentials to RTSP URL if needed
            if decoded_uri.startswith("rtsp://") and "@" not in decoded_uri:
                camera = entry.runtime_data.camera
                decoded_uri = decoded_uri.replace(
                    "rtsp://",
                    f"rtsp://{camera.usr}:{camera.pwd}@",
                    1,
                )
            rtsp_url = decoded_uri
        else:
            # Construct RTSP URL from parameters
            camera = entry.runtime_data.camera
            host = camera.root_url.replace("http://", "").replace("https://", "")
            rtsp_url = (
                f"rtsp://{camera.usr}:{camera.pwd}@{host}:554/"
                f"Streaming/tracks/{track_id_str}/"
                f"?starttime={start_time_str}&endtime={end_time_str}"
            )

        _LOGGER.debug("Creating stream for playback URL: %s", rtsp_url.split("@")[-1])

        # Create an HLS stream for playback
        stream = create_stream(self.hass, rtsp_url, {}, DynamicStreamSettings())
        stream.add_provider("hls", timeout=3600)
        stream_url: str = stream.endpoint_url("hls")

        return PlayMedia(stream_url, "application/x-mpegURL")

    async def async_browse_media(
        self,
        item: MediaSourceItem,
    ) -> BrowseMediaSource:
        """Browse media."""
        if not item.identifier:
            return await self._async_generate_root()

        identifier = item.identifier.split("|")
        item_type = identifier[0]

        if item_type == "DEVICE":
            _, config_entry_id = identifier
            return await self._async_generate_channels(config_entry_id)

        if item_type == "CHANNEL":
            _, config_entry_id, track_id = identifier
            return await self._async_generate_days(config_entry_id, int(track_id))

        if item_type == "DAY":
            _, config_entry_id, track_id, year, month, day = identifier
            return await self._async_generate_recordings(
                config_entry_id,
                int(track_id),
                int(year),
                int(month),
                int(day),
            )

        raise Unresolvable(f"Unknown media item '{item.identifier}'")

    def _get_config_entry(self, config_entry_id: str) -> HikvisionConfigEntry | None:
        """Get a config entry by ID."""
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            if entry.entry_id == config_entry_id:
                return entry
        return None

    async def _async_generate_root(self) -> BrowseMediaSource:
        """Generate the root browsing structure with all Hikvision devices."""
        children: list[BrowseMediaSource] = []

        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            device_name = entry.runtime_data.device_name
            device_type = entry.runtime_data.device_type

            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"DEVICE|{entry.entry_id}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"{device_name} ({device_type})",
                    can_play=False,
                    can_expand=True,
                )
            )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.APP,
            media_content_type="",
            title="Hikvision",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_generate_channels(
        self, config_entry_id: str
    ) -> BrowseMediaSource:
        """Generate the channel list for a device."""
        entry = self._get_config_entry(config_entry_id)
        if entry is None:
            raise Unresolvable(f"Config entry {config_entry_id} not found")

        client = await self.hass.async_add_executor_job(
            HikvisionISAPIClient, entry.runtime_data.camera
        )
        channels = await self.hass.async_add_executor_job(client.get_channels)

        children: list[BrowseMediaSource] = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"CHANNEL|{config_entry_id}|{channel['id']}",
                media_class=MediaClass.CHANNEL,
                media_content_type=MediaType.PLAYLIST,
                title=channel["name"],
                can_play=False,
                can_expand=True,
            )
            for channel in channels
        ]

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"DEVICE|{config_entry_id}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=entry.runtime_data.device_name,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_generate_days(
        self, config_entry_id: str, track_id: int
    ) -> BrowseMediaSource:
        """Generate the list of days with recordings."""
        entry = self._get_config_entry(config_entry_id)
        if entry is None:
            raise Unresolvable(f"Config entry {config_entry_id} not found")

        client = await self.hass.async_add_executor_job(
            HikvisionISAPIClient, entry.runtime_data.camera
        )

        # Search for recordings in the last 30 days
        now = datetime.now()
        start_date = now - timedelta(days=30)

        recording_days = await self.hass.async_add_executor_job(
            client.get_recording_days, track_id, start_date, now
        )

        children: list[BrowseMediaSource] = []
        for day in recording_days:
            date_str = day.date.strftime("%Y-%m-%d")
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=(
                        f"DAY|{config_entry_id}|{track_id}|"
                        f"{day.date.year}|{day.date.month}|{day.date.day}"
                    ),
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.PLAYLIST,
                    title=date_str,
                    can_play=False,
                    can_expand=True,
                )
            )

        # Get channel name for title
        channels = await self.hass.async_add_executor_job(client.get_channels)
        channel_name = f"Channel {track_id}"
        for channel in channels:
            if channel["id"] == str(track_id):
                channel_name = channel["name"]
                break

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"CHANNEL|{config_entry_id}|{track_id}",
            media_class=MediaClass.CHANNEL,
            media_content_type=MediaType.PLAYLIST,
            title=f"{channel_name} - Recordings",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_generate_recordings(
        self,
        config_entry_id: str,
        track_id: int,
        year: int,
        month: int,
        day: int,
    ) -> BrowseMediaSource:
        """Generate the list of recordings for a specific day."""
        entry = self._get_config_entry(config_entry_id)
        if entry is None:
            raise Unresolvable(f"Config entry {config_entry_id} not found")

        client = await self.hass.async_add_executor_job(
            HikvisionISAPIClient, entry.runtime_data.camera
        )

        # Search for recordings on the specific day
        start_time = datetime(year, month, day, 0, 0, 0)
        end_time = datetime(year, month, day, 23, 59, 59)

        recordings = await self.hass.async_add_executor_job(
            client.search_recordings, track_id, start_time, end_time
        )

        children: list[BrowseMediaSource] = []
        for recording in recordings:
            # Calculate duration
            duration = recording.end_time - recording.start_time
            duration_str = str(duration).split(".")[0]  # Remove microseconds

            # Format title with time and duration
            time_str = recording.start_time.strftime("%H:%M:%S")
            title = f"{time_str} ({duration_str})"

            # Encode the playback URI
            encoded_uri = quote_plus(recording.playback_uri) if recording.playback_uri else ""

            # Create identifier for this recording
            start_str = recording.start_time.strftime("%Y%m%dT%H%M%SZ")
            end_str = recording.end_time.strftime("%Y%m%dT%H%M%SZ")

            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=(
                        f"FILE|{config_entry_id}|{track_id}|"
                        f"{start_str}|{end_str}|{encoded_uri}"
                    ),
                    media_class=MediaClass.VIDEO,
                    media_content_type=MediaType.VIDEO,
                    title=title,
                    can_play=True,
                    can_expand=False,
                )
            )

        date_str = f"{year}-{month:02d}-{day:02d}"

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"DAY|{config_entry_id}|{track_id}|{year}|{month}|{day}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=f"Recordings - {date_str}",
            can_play=False,
            can_expand=True,
            children=children,
        )
