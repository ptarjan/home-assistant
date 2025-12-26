"""Expose Hikvision camera recordings as media sources."""

from __future__ import annotations

import asyncio
from base64 import urlsafe_b64decode, urlsafe_b64encode
import binascii
from datetime import datetime, timedelta
import logging
import os
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import quote_plus, unquote

import aiofiles
from aiohttp import web
import httpx

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from . import HikvisionConfigEntry
from .const import DOMAIN
from .isapi import HikvisionISAPIClient

_LOGGER = logging.getLogger(__name__)


def _get_hls_dir(stream_id: str) -> str:
    """Get the HLS output directory for a stream."""
    return os.path.join(tempfile.gettempdir(), "hikvision_hls", stream_id)


class HikvisionHLSView(HomeAssistantView):
    """View to stream Hikvision recordings as HLS using ffmpeg subprocess."""

    url = "/api/hikvision/hls/{entry_id}/{stream_id}"
    name = "api:hikvision:hls"
    requires_auth = False

    # Class-level state - only one stream at a time due to NVR bandwidth limits
    _active_ffmpeg: dict[str, Any] = {}
    _active_stream_id: str | None = None
    _stderr_task: asyncio.Task[None] | None = None

    async def _serve_segment(self, hls_dir: str, segment: str) -> web.Response:
        """Serve an HLS segment file."""
        segment_path = os.path.join(hls_dir, segment)
        if os.path.exists(segment_path):
            async with aiofiles.open(segment_path, "rb") as f:
                data = await f.read()
            return web.Response(body=data, content_type="video/mp2t")
        return web.Response(status=404, text="Segment not found")

    def _apply_offset_to_uri(self, rtsp_uri: str, offset_seconds: int) -> str:
        """Modify RTSP URI to apply time offset."""
        if offset_seconds <= 0 or "starttime=" not in rtsp_uri:
            return rtsp_uri

        match = re.search(r"starttime=(\d{8}T\d{6})Z", rtsp_uri)
        if match:
            start_str = match.group(1)
            start_time = datetime.strptime(start_str, "%Y%m%dT%H%M%S")
            new_start = start_time + timedelta(seconds=offset_seconds)
            new_start_str = new_start.strftime("%Y%m%dT%H%M%S")
            rtsp_uri = rtsp_uri.replace(
                f"starttime={start_str}Z", f"starttime={new_start_str}Z"
            )
            _LOGGER.debug(
                "Seeking to offset %ds: new starttime=%s",
                offset_seconds,
                new_start_str,
            )
        return rtsp_uri

    async def _cleanup_old_streams(self, current_stream_id: str) -> None:
        """Kill any existing ffmpeg processes to free bandwidth."""
        for old_stream_id, proc in list(self._active_ffmpeg.items()):
            if old_stream_id != current_stream_id and proc.returncode is None:
                _LOGGER.debug("Killing old ffmpeg stream: %s", old_stream_id)
                proc.kill()
                await proc.wait()
                old_dir = _get_hls_dir(old_stream_id)
                if os.path.exists(old_dir):
                    shutil.rmtree(old_dir, ignore_errors=True)
        self._active_ffmpeg = {
            k: v for k, v in self._active_ffmpeg.items() if k == current_stream_id
        }

    @classmethod
    async def async_cleanup_all(cls) -> None:
        """Clean up all active ffmpeg processes."""
        for proc in cls._active_ffmpeg.values():
            if proc.returncode is None:
                proc.kill()
        cls._active_ffmpeg.clear()

    async def _start_ffmpeg(
        self, stream_id: str, rtsp_uri: str, hls_dir: str, playlist_path: str
    ) -> asyncio.subprocess.Process:
        """Start ffmpeg process for HLS streaming."""
        await self._cleanup_old_streams(stream_id)

        # Use run_in_executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        if os.path.exists(hls_dir):
            await loop.run_in_executor(
                None, lambda: shutil.rmtree(hls_dir, ignore_errors=True)
            )
        await loop.run_in_executor(None, lambda: os.makedirs(hls_dir, exist_ok=True))

        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "warning",
            "-fflags",
            "+genpts+discardcorrupt",
            "-use_wallclock_as_timestamps",
            "1",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            "10000000",
            "-i",
            rtsp_uri,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-vf",
            "scale=-2:720",
            "-b:v",
            "2M",
            "-f",
            "hls",
            "-hls_time",
            "2",
            "-hls_list_size",
            "0",
            "-hls_flags",
            "append_list",
            playlist_path,
        ]
        _LOGGER.debug("Starting ffmpeg for stream %s", stream_id)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        self._active_ffmpeg[stream_id] = proc
        self._active_stream_id = stream_id

        async def log_stderr() -> None:
            if proc.stderr:
                stderr = await proc.stderr.read()
                if stderr:
                    _LOGGER.warning("Ffmpeg stderr: %s", stderr.decode()[:1000])

        self._stderr_task = asyncio.create_task(log_stderr())
        return proc

    async def _wait_for_playlist(
        self, stream_id: str, hls_dir: str, playlist_path: str
    ) -> web.Response | None:
        """Wait for playlist to be created. Returns error response or None on success."""
        for _ in range(60):
            if os.path.exists(playlist_path):
                try:
                    segments = [f for f in os.listdir(hls_dir) if f.endswith(".ts")]
                    if segments:
                        seg_path = os.path.join(hls_dir, segments[0])
                        if os.path.getsize(seg_path) > 10000:
                            _LOGGER.debug(
                                "Stream %s ready: %d segments", stream_id, len(segments)
                            )
                            return None
                except (OSError, IndexError):
                    pass
            await asyncio.sleep(0.5)
            if stream_id in self._active_ffmpeg:
                proc = self._active_ffmpeg[stream_id]
                if proc.returncode is not None and proc.returncode != 0:
                    _LOGGER.error("Ffmpeg exited with code %s", proc.returncode)
                    return web.Response(
                        status=503, text=f"Stream failed (code {proc.returncode})"
                    )
        return None

    async def get(
        self, request: web.Request, entry_id: str, stream_id: str
    ) -> web.Response:
        """Handle GET request for HLS playlist or segment."""
        rtsp_uri = request.query.get("uri", "")
        segment = request.query.get("segment", "")
        offset_str = request.query.get("offset", "0")

        if not rtsp_uri and not segment:
            return web.Response(status=400, text="Missing uri parameter")

        try:
            offset_seconds = int(offset_str)
        except ValueError:
            offset_seconds = 0

        if offset_seconds > 0:
            stream_id = f"{stream_id}_{offset_seconds}"

        hls_dir = _get_hls_dir(stream_id)
        os.makedirs(hls_dir, exist_ok=True)
        playlist_path = os.path.join(hls_dir, "stream.m3u8")

        if segment:
            return await self._serve_segment(hls_dir, segment)

        rtsp_uri = self._apply_offset_to_uri(unquote(rtsp_uri), offset_seconds)

        need_start = (
            stream_id not in self._active_ffmpeg
            or self._active_ffmpeg[stream_id].returncode is not None
        )
        if need_start:
            await self._start_ffmpeg(stream_id, rtsp_uri, hls_dir, playlist_path)

        if error := await self._wait_for_playlist(stream_id, hls_dir, playlist_path):
            return error

        if not os.path.exists(playlist_path):
            return web.Response(status=503, text="Waiting for stream...")

        async with aiofiles.open(playlist_path) as f:
            playlist = await f.read()

        lines = [
            f"/api/hikvision/hls/{entry_id}/{stream_id}?segment={line}"
            if line.endswith(".ts")
            else line
            for line in playlist.split("\n")
        ]

        return web.Response(
            text="\n".join(lines), content_type="application/vnd.apple.mpegurl"
        )


def _cleanup_hls_temp_dir() -> None:
    """Clean up HLS temp directory."""
    hls_base = os.path.join(tempfile.gettempdir(), "hikvision_hls")
    if os.path.exists(hls_base):
        shutil.rmtree(hls_base, ignore_errors=True)


class HikvisionDownloadView(HomeAssistantView):
    """View to proxy video downloads from Hikvision via HTTP (bypasses RTSP limit)."""

    url = "/api/hikvision/download/{config_entry_id}/{filename}"
    name = "api:hikvision:download"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the download view."""
        self.hass = hass

    async def get(
        self, request: web.Request, config_entry_id: str, filename: str
    ) -> web.StreamResponse:
        """Handle GET request for video download."""
        # Decode the base64 filename to get the playback URI
        try:
            playback_uri = urlsafe_b64decode(filename.encode("utf-8")).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError) as err:
            _LOGGER.error("Failed to decode playback URI: %s", err)
            return web.Response(status=400, text="Invalid filename encoding")

        # Get config entry
        entry = None
        for e in self.hass.config_entries.async_loaded_entries(DOMAIN):
            if e.entry_id == config_entry_id:
                entry = e
                break

        if entry is None:
            return web.Response(status=404, text="Config entry not found")

        # Get credentials from the ISAPI client
        client = HikvisionISAPIClient(entry.runtime_data.camera)
        base_url = client.base_url
        username = client.username
        password = client.password

        # Build the download request XML
        download_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<downloadRequest>
<playbackURI>{playback_uri}</playbackURI>
</downloadRequest>"""

        download_url = f"{base_url}/ISAPI/ContentMgmt/download"
        _LOGGER.debug("Starting HTTP download from: %s", download_url)

        try:
            # Use httpx with digest auth for async streaming
            auth = httpx.DigestAuth(username, password)
            async with (
                httpx.AsyncClient(
                    auth=auth,
                    verify=False,
                    timeout=httpx.Timeout(connect=15, read=None),
                ) as http_client,
                http_client.stream(
                    "POST",
                    download_url,
                    content=download_xml,
                    headers={"Content-Type": "application/xml"},
                ) as resp,
            ):
                if resp.status_code != 200:
                    _LOGGER.error(
                        "Download failed: HTTP %s - %s",
                        resp.status_code,
                        resp.text[:200] if hasattr(resp, "text") else "unknown",
                    )
                    return web.Response(status=resp.status_code, text="Download failed")

                # Get content type from response
                content_type = resp.headers.get("content-type", "video/mp4")
                if "octet-stream" in content_type:
                    content_type = "video/mp4"

                # Create streaming response
                response = web.StreamResponse(
                    status=200,
                    headers={
                        "Content-Type": content_type,
                        "Content-Disposition": "inline",
                    },
                )
                await response.prepare(request)

                # Stream chunks to browser
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    await response.write(chunk)

                await response.write_eof()
                return response

        except httpx.TimeoutException as err:
            _LOGGER.error("Download timeout: %s", err)
            return web.Response(status=504, text="Download timeout")
        except httpx.HTTPError as err:
            _LOGGER.error("Download HTTP error: %s", err)
            return web.Response(status=502, text=f"Download error: {err}")


def _generate_download_url(config_entry_id: str, playback_uri: str) -> str:
    """Generate a download proxy URL for a recording."""
    encoded = urlsafe_b64encode(playback_uri.encode("utf-8")).decode("utf-8")
    return f"/api/hikvision/download/{config_entry_id}/{encoded}"


async def async_get_media_source(hass: HomeAssistant) -> HikvisionMediaSource:
    """Set up Hikvision media source."""
    # Clean up any stale temp files from previous runs
    await hass.async_add_executor_job(_cleanup_hls_temp_dir)

    # Register the HTTP download view (preferred - bypasses RTSP connection limit)
    hass.http.register_view(HikvisionDownloadView(hass))

    # Register the custom HLS view as fallback
    hass.http.register_view(HikvisionHLSView())

    # Register cleanup on shutdown
    async def _async_cleanup(_event: Any) -> None:
        """Clean up on shutdown."""
        # Kill any running ffmpeg processes
        await HikvisionHLSView.async_cleanup_all()
        # Clean up temp files
        await hass.async_add_executor_job(_cleanup_hls_temp_dir)

    hass.bus.async_listen_once("homeassistant_stop", _async_cleanup)

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
        _LOGGER.debug("async_resolve_media called with identifier: %s", item.identifier)
        if item.identifier is None:
            raise Unresolvable("No media item identifier provided")

        identifier = item.identifier.split("|")
        item_type = identifier[0]
        offset_seconds = 0

        # Handle FILE, RECORDING, and TIMESLOT types
        if item_type in {"FILE", "RECORDING"}:
            if len(identifier) < 6:
                raise Unresolvable(
                    f"Invalid media identifier format: {item.identifier}"
                )
            config_entry_id = identifier[1]
            track_id_str = identifier[2]
            start_time_str = identifier[3]
            end_time_str = identifier[4]
            playback_uri = identifier[5] if len(identifier) > 5 else ""
        elif item_type == "TIMESLOT":
            if len(identifier) < 7:
                raise Unresolvable(
                    f"Invalid timeslot identifier format: {item.identifier}"
                )
            config_entry_id = identifier[1]
            track_id_str = identifier[2]
            start_time_str = identifier[3]
            end_time_str = identifier[4]
            offset_seconds = int(identifier[5])
            playback_uri = identifier[6] if len(identifier) > 6 else ""
        else:
            raise Unresolvable(f"Unknown media item type '{item_type}'")

        _LOGGER.debug(
            "Parsed: track=%s, start=%s, end=%s, offset=%ds, uri=%s",
            track_id_str,
            start_time_str,
            end_time_str,
            offset_seconds,
            playback_uri[:50] if playback_uri else "none",
        )

        entry = self._get_config_entry(config_entry_id)
        if entry is None:
            raise Unresolvable(f"Config entry {config_entry_id} not found")

        # Construct the playback URI for HTTP download (bypasses RTSP connection limit)
        if playback_uri:
            # Decode the playback URI (it's URL-encoded from the identifier)
            decoded_uri = unquote(playback_uri)
            _LOGGER.debug("Decoded URI: %s", decoded_uri)
        else:
            # Construct playback URI from parameters
            camera = entry.runtime_data.camera
            host = camera.root_url.replace("http://", "").replace("https://", "")
            # Remove port if present for RTSP URL
            if ":" in host:
                host = host.split(":")[0]
            # Convert channel ID to track ID
            actual_track_id = int(track_id_str) * 100 + 1
            decoded_uri = (
                f"rtsp://{host}:554/Streaming/tracks/{actual_track_id}/"
                f"?starttime={start_time_str}&endtime={end_time_str}"
            )

        # Apply time offset by modifying the starttime in the URI
        if offset_seconds > 0 and "starttime=" in decoded_uri:
            match = re.search(r"starttime=(\d{8}T\d{6})Z", decoded_uri)
            if match:
                start_str = match.group(1)
                start_time = datetime.strptime(start_str, "%Y%m%dT%H%M%S")
                new_start = start_time + timedelta(seconds=offset_seconds)
                new_start_str = new_start.strftime("%Y%m%dT%H%M%S")
                decoded_uri = decoded_uri.replace(
                    f"starttime={start_str}Z", f"starttime={new_start_str}Z"
                )
                _LOGGER.debug(
                    "Seeking to offset %ds: new starttime=%s",
                    offset_seconds,
                    new_start_str,
                )

        # Use HTTP download endpoint (bypasses RTSP connection limit!)
        # The playback URI is passed to /ISAPI/ContentMgmt/download via POST
        download_url = _generate_download_url(config_entry_id, decoded_uri)
        _LOGGER.debug("Using HTTP download (no RTSP): %s", download_url[:80])

        return PlayMedia(download_url, "video/mp4")

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

        if item_type == "RECORDING":
            _, config_entry_id, track_id, start_str, end_str, encoded_uri = identifier
            return await self._async_generate_time_slots(
                config_entry_id,
                int(track_id),
                start_str,
                end_str,
                encoded_uri,
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

    async def _async_generate_channels(self, config_entry_id: str) -> BrowseMediaSource:
        """Generate the channel list for a device."""
        entry = self._get_config_entry(config_entry_id)
        if entry is None:
            raise Unresolvable(f"Config entry {config_entry_id} not found")

        client = await self.hass.async_add_executor_job(
            HikvisionISAPIClient, entry.runtime_data.camera
        )
        channels = await self.hass.async_add_executor_job(client.get_channels)
        _LOGGER.debug(
            "Got channels for %s: %s", entry.runtime_data.device_name, channels
        )

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

        # Convert channel ID to track ID (channel 1 = track 101, channel 2 = track 201, etc.)
        actual_track_id = track_id * 100 + 1
        _LOGGER.debug(
            "Searching recording days for channel %s (track %s) from %s to %s",
            track_id,
            actual_track_id,
            start_date,
            now,
        )

        recording_days = await self.hass.async_add_executor_job(
            client.get_recording_days, actual_track_id, start_date, now
        )
        _LOGGER.debug("Found %s recording days", len(recording_days))

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

        # Convert channel ID to track ID (channel 1 = track 101, channel 2 = track 201, etc.)
        actual_track_id = track_id * 100 + 1

        recordings = await self.hass.async_add_executor_job(
            client.search_recordings, actual_track_id, start_time, end_time
        )

        children: list[BrowseMediaSource] = []
        for recording in recordings:
            # Calculate duration for display
            duration = recording.end_time - recording.start_time
            duration_str = str(duration).split(".", maxsplit=1)[
                0
            ]  # Remove microseconds

            # Format title with time and duration
            time_str = recording.start_time.strftime("%H:%M:%S")
            title = f"{time_str} ({duration_str})"

            # Encode the playback URI
            encoded_uri = (
                quote_plus(recording.playback_uri) if recording.playback_uri else ""
            )

            # Create identifier for this recording
            start_str = recording.start_time.strftime("%Y%m%dT%H%M%SZ")
            end_str = recording.end_time.strftime("%Y%m%dT%H%M%SZ")

            # All recordings are expandable with time slots
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=(
                        f"RECORDING|{config_entry_id}|{track_id}|"
                        f"{start_str}|{end_str}|{encoded_uri}"
                    ),
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.PLAYLIST,
                    title=f"[+] {title}",
                    can_play=False,
                    can_expand=True,
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

    async def _async_generate_time_slots(
        self,
        config_entry_id: str,
        track_id: int,
        start_str: str,
        end_str: str,
        encoded_uri: str,
    ) -> BrowseMediaSource:
        """Generate 15-minute time slots for a recording."""
        # Parse start and end times
        start_time = datetime.strptime(start_str, "%Y%m%dT%H%M%SZ")
        end_time = datetime.strptime(end_str, "%Y%m%dT%H%M%SZ")
        duration = end_time - start_time
        duration_seconds = int(duration.total_seconds())

        children: list[BrowseMediaSource] = []

        # Generate 5-minute time slots for finer control
        slot_duration = 300  # 5 minutes in seconds
        offset = 0

        while offset < duration_seconds:
            slot_time = start_time + timedelta(seconds=offset)
            slot_time_str = slot_time.strftime("%H:%M:%S")

            # Calculate remaining time for this slot
            remaining = duration_seconds - offset
            slot_len = min(slot_duration, remaining)
            slot_len_str = f"{slot_len // 60}:{slot_len % 60:02d}"

            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=(
                        f"TIMESLOT|{config_entry_id}|{track_id}|"
                        f"{start_str}|{end_str}|{offset}|{encoded_uri}"
                    ),
                    media_class=MediaClass.VIDEO,
                    media_content_type=MediaType.VIDEO,
                    title=f"{slot_time_str} ({slot_len_str})",
                    can_play=True,
                    can_expand=False,
                )
            )

            offset += slot_duration

        recording_title = (
            f"{start_time.strftime('%H:%M:%S')} - {end_time.strftime('%H:%M:%S')}"
        )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"RECORDING|{config_entry_id}|{track_id}|{start_str}|{end_str}|{encoded_uri}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=f"Recording {recording_title}",
            can_play=True,
            can_expand=True,
            children=children,
        )
