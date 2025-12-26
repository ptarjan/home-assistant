"""ISAPI client for Hikvision devices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

import defusedxml.ElementTree as DefusedET
import requests
from requests.auth import HTTPDigestAuth

if TYPE_CHECKING:
    from pyhik.hikvision import HikCamera

_LOGGER = logging.getLogger(__name__)

# ISAPI namespaces
ISAPI_NS = "http://www.hikvision.com/ver20/XMLSchema"
NS = {"ns": ISAPI_NS}


@dataclass
class Recording:
    """Represents a recording from the Hikvision device."""

    source_id: str
    track_id: int
    start_time: datetime
    end_time: datetime
    content_type: str
    playback_uri: str


@dataclass
class RecordingDay:
    """Represents a day with recordings available."""

    date: datetime
    has_recordings: bool


class HikvisionISAPIClient:
    """Client for Hikvision ISAPI ContentMgmt operations."""

    def __init__(self, camera: HikCamera) -> None:
        """Initialize the ISAPI client.

        Args:
            camera: The HikCamera instance to use for connection details.

        """
        self._camera = camera
        self._base_url = f"{camera.root_url}:{camera.port}"
        self._auth = HTTPDigestAuth(camera.usr, camera.pwd)
        self._session = requests.Session()
        self._session.auth = self._auth
        self._session.verify = False  # Many Hikvision devices use self-signed certs

    def get_channels(self) -> list[dict]:
        """Get available channels from the device.

        Returns:
            List of channel dictionaries with 'id' and 'name' keys.

        """
        channels = []
        try:
            response = self._session.get(
                f"{self._base_url}/ISAPI/ContentMgmt/InputProxy/channels",
                timeout=10,
            )
            if response.status_code == 200:
                root = DefusedET.fromstring(response.content)
                for channel in root.findall(".//ns:InputProxyChannel", NS):
                    channel_id = channel.find("ns:id", NS)
                    channel_name = channel.find("ns:name", NS)
                    if channel_id is not None:
                        channels.append({
                            "id": channel_id.text,
                            "name": (
                                channel_name.text
                                if channel_name is not None
                                else f"Channel {channel_id.text}"
                            ),
                        })
        except (requests.RequestException, ET.ParseError) as err:
            _LOGGER.debug("Failed to get channels: %s", err)

        # If no channels found via InputProxy, try streaming channels
        if not channels:
            channels = self._get_streaming_channels()

        # If still no channels, assume single channel device
        if not channels:
            device_name = self._camera.get_name or "Camera"
            channels = [{"id": "101", "name": device_name}]

        return channels

    def _get_streaming_channels(self) -> list[dict]:
        """Get streaming channels from the device.

        Returns:
            List of channel dictionaries.

        """
        channels = []
        try:
            response = self._session.get(
                f"{self._base_url}/ISAPI/Streaming/channels",
                timeout=10,
            )
            if response.status_code == 200:
                root = DefusedET.fromstring(response.content)
                seen_channels = set()
                for channel in root.findall(".//ns:StreamingChannel", NS):
                    channel_id = channel.find("ns:id", NS)
                    channel_name = channel.find("ns:channelName", NS)
                    if channel_id is not None:
                        # Extract main channel ID (e.g., "101" from "101", "102" from "102")
                        # Track IDs are typically 101, 201, 301... for main streams
                        cid = channel_id.text
                        if cid and cid.endswith("01") and cid not in seen_channels:
                            seen_channels.add(cid)
                            channels.append({
                                "id": cid,
                                "name": (
                                    channel_name.text
                                    if channel_name is not None
                                    else f"Channel {cid}"
                                ),
                            })
        except (requests.RequestException, ET.ParseError) as err:
            _LOGGER.debug("Failed to get streaming channels: %s", err)

        return channels

    def get_recording_days(
        self,
        track_id: int,
        start_date: datetime,
        end_date: datetime,
    ) -> list[RecordingDay]:
        """Get days with recordings available.

        Args:
            track_id: The track ID to search (e.g., 101 for channel 1).
            start_date: Start of the search range.
            end_date: End of the search range.

        Returns:
            List of RecordingDay objects.

        """
        search_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<CMSearchDescription>
    <searchID>recording-days-search</searchID>
    <trackList>
        <trackID>{track_id}</trackID>
    </trackList>
    <timeSpanList>
        <timeSpan>
            <startTime>{start_date.strftime('%Y-%m-%dT%H:%M:%SZ')}</startTime>
            <endTime>{end_date.strftime('%Y-%m-%dT%H:%M:%SZ')}</endTime>
        </timeSpan>
    </timeSpanList>
    <maxResults>1000</maxResults>
    <searchResultPosition>0</searchResultPosition>
    <metadataList>
        <metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor>
    </metadataList>
</CMSearchDescription>"""

        days_with_recordings: dict[str, RecordingDay] = {}

        try:
            response = self._session.post(
                f"{self._base_url}/ISAPI/ContentMgmt/search",
                data=search_xml,
                headers={"Content-Type": "application/xml"},
                timeout=30,
            )

            if response.status_code == 200:
                root = DefusedET.fromstring(response.content)

                for match in root.findall(".//ns:searchMatchItem", NS):
                    time_span = match.find("ns:timeSpan", NS)
                    if time_span is not None:
                        start_time = time_span.find("ns:startTime", NS)
                        if start_time is not None and start_time.text:
                            rec_date = datetime.fromisoformat(start_time.text)
                            date_key = rec_date.strftime("%Y-%m-%d")
                            if date_key not in days_with_recordings:
                                days_with_recordings[date_key] = RecordingDay(
                                    date=rec_date.replace(
                                        hour=0, minute=0, second=0, microsecond=0
                                    ),
                                    has_recordings=True,
                                )

        except (requests.RequestException, ET.ParseError) as err:
            _LOGGER.warning("Failed to get recording days: %s", err)

        return sorted(
            days_with_recordings.values(), key=lambda x: x.date, reverse=True
        )

    def search_recordings(
        self,
        track_id: int,
        start_time: datetime,
        end_time: datetime,
        max_results: int = 100,
    ) -> list[Recording]:
        """Search for recordings in a time range.

        Args:
            track_id: The track ID to search (e.g., 101 for channel 1).
            start_time: Start of the search range.
            end_time: End of the search range.
            max_results: Maximum number of results to return.

        Returns:
            List of Recording objects.

        """
        search_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<CMSearchDescription>
    <searchID>recording-search</searchID>
    <trackList>
        <trackID>{track_id}</trackID>
    </trackList>
    <timeSpanList>
        <timeSpan>
            <startTime>{start_time.strftime('%Y-%m-%dT%H:%M:%SZ')}</startTime>
            <endTime>{end_time.strftime('%Y-%m-%dT%H:%M:%SZ')}</endTime>
        </timeSpan>
    </timeSpanList>
    <maxResults>{max_results}</maxResults>
    <searchResultPosition>0</searchResultPosition>
    <metadataList>
        <metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor>
    </metadataList>
</CMSearchDescription>"""

        recordings: list[Recording] = []

        try:
            response = self._session.post(
                f"{self._base_url}/ISAPI/ContentMgmt/search",
                data=search_xml,
                headers={"Content-Type": "application/xml"},
                timeout=30,
            )

            if response.status_code == 200:
                root = DefusedET.fromstring(response.content)
                recordings = self._parse_search_results(root)

        except (requests.RequestException, ET.ParseError) as err:
            _LOGGER.warning("Failed to search recordings: %s", err)

        return recordings

    def _parse_search_results(self, root: ET.Element) -> list[Recording]:
        """Parse search results from XML response.

        Args:
            root: The root element of the XML response.

        Returns:
            List of Recording objects.

        """
        recordings: list[Recording] = []

        for match in root.findall(".//ns:searchMatchItem", NS):
            try:
                source_id_elem = match.find("ns:sourceID", NS)
                track_id_elem = match.find("ns:trackID", NS)
                time_span = match.find("ns:timeSpan", NS)
                media_segment = match.find(
                    "ns:mediaSegmentDescriptor/ns:playbackURI", NS
                )

                if time_span is None:
                    continue

                start_time_elem = time_span.find("ns:startTime", NS)
                end_time_elem = time_span.find("ns:endTime", NS)

                if (
                    start_time_elem is None
                    or end_time_elem is None
                    or start_time_elem.text is None
                    or end_time_elem.text is None
                ):
                    continue

                # Parse the times
                rec_start = datetime.fromisoformat(start_time_elem.text)
                rec_end = datetime.fromisoformat(end_time_elem.text)

                # Get playback URI
                playback_uri = ""
                if media_segment is not None and media_segment.text:
                    playback_uri = media_segment.text

                # Get content type
                content_type_elem = match.find(
                    "ns:mediaSegmentDescriptor/ns:contentType", NS
                )
                content_type = (
                    content_type_elem.text
                    if content_type_elem is not None and content_type_elem.text
                    else "video"
                )

                recordings.append(
                    Recording(
                        source_id=(
                            source_id_elem.text
                            if source_id_elem is not None and source_id_elem.text
                            else ""
                        ),
                        track_id=(
                            int(track_id_elem.text)
                            if track_id_elem is not None and track_id_elem.text
                            else 101
                        ),
                        start_time=rec_start,
                        end_time=rec_end,
                        content_type=content_type,
                        playback_uri=playback_uri,
                    )
                )

            except (ValueError, AttributeError) as err:
                _LOGGER.debug("Failed to parse recording: %s", err)
                continue

        return sorted(recordings, key=lambda x: x.start_time, reverse=True)

    def get_playback_url(self, recording: Recording) -> str:
        """Get the playback URL for a recording.

        For RTSP URLs, we return them directly for streaming.
        For other URLs, we may need to construct them from the recording data.

        Args:
            recording: The recording to get the playback URL for.

        Returns:
            The playback URL string.

        """
        if recording.playback_uri:
            # If the URI is an RTSP URL, return it with credentials
            if recording.playback_uri.startswith("rtsp://"):
                # Insert credentials into RTSP URL
                # Replace rtsp:// with rtsp://user:pass@
                return recording.playback_uri.replace(
                    "rtsp://",
                    f"rtsp://{self._camera.usr}:{self._camera.pwd}@",
                    1,
                )
            return recording.playback_uri

        # Construct RTSP URL from recording data
        host = self._camera.root_url.replace("http://", "").replace("https://", "")
        start_str = recording.start_time.strftime("%Y%m%dT%H%M%SZ")
        end_str = recording.end_time.strftime("%Y%m%dT%H%M%SZ")

        return (
            f"rtsp://{self._camera.usr}:{self._camera.pwd}@{host}:554/"
            f"Streaming/tracks/{recording.track_id}/"
            f"?starttime={start_str}&endtime={end_str}"
        )
