"""Helper functions for Hikvision integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging

import defusedxml.ElementTree as ET
import requests
from requests.auth import HTTPDigestAuth

_LOGGER = logging.getLogger(__name__)


@dataclass
class HikvisionChannel:
    """Represents a video input channel on a Hikvision device."""

    id: int
    name: str
    enabled: bool = True


def get_video_channels(
    host: str,
    port: int,
    username: str,
    password: str,
    ssl: bool = False,
) -> list[HikvisionChannel]:
    """Fetch available video input channels from Hikvision device.

    This queries the ISAPI to discover available camera channels on
    NVRs and standalone cameras.

    Returns a list of HikvisionChannel objects.
    """
    protocol = "https" if ssl else "http"
    root_url = f"{protocol}://{host}:{port}"
    channels: list[HikvisionChannel] = []

    session = requests.Session()
    session.auth = HTTPDigestAuth(username, password)

    # Try different ISAPI endpoints for channel discovery
    urls = [
        f"{root_url}/ISAPI/System/Video/inputs/channels",
        f"{root_url}/ISAPI/ContentMgmt/InputProxy/channels",
    ]

    response = None
    for url in urls:
        try:
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                break
        except requests.exceptions.RequestException:
            continue

    if response is None or response.status_code != 200:
        _LOGGER.debug("Unable to fetch video channels from device, trying streaming")
        # Fall back to streaming channels endpoint
        try:
            response = session.get(f"{root_url}/ISAPI/Streaming/channels", timeout=10)
        except requests.exceptions.RequestException:
            session.close()
            return channels

    if response is None or response.status_code != 200:
        _LOGGER.warning("Unable to fetch video channels from device")
        session.close()
        return channels

    try:
        tree = ET.fromstring(response.text)
    except ET.ParseError as err:
        _LOGGER.error("Failed to parse video channels XML: %s", err)
        session.close()
        return channels

    # Handle namespace
    namespace = ""
    root_tag = tree.tag
    if root_tag.startswith("{"):
        namespace = root_tag.split("}")[0] + "}"

    # Try to find VideoInputChannel elements (from /System/Video/inputs/channels)
    channel_elements = tree.findall(f".//{namespace}VideoInputChannel")

    # If not found, try InputProxyChannel (from /ContentMgmt/InputProxy/channels)
    if not channel_elements:
        channel_elements = tree.findall(f".//{namespace}InputProxyChannel")

    # If still not found, try StreamingChannel (from /Streaming/channels)
    if not channel_elements:
        channel_elements = tree.findall(f".//{namespace}StreamingChannel")
        # Streaming channels have different structure - extract unique channel IDs
        seen_channels: set[int] = set()
        for elem in channel_elements:
            channel_id_elem = elem.find(f"{namespace}id")
            if channel_id_elem is not None and channel_id_elem.text:
                try:
                    # Channel IDs are formatted as (channel * 100) + stream_type
                    # e.g., 101 = channel 1 main, 102 = channel 1 sub
                    full_id = int(channel_id_elem.text)
                    channel_num = full_id // 100
                    if channel_num > 0 and channel_num not in seen_channels:
                        seen_channels.add(channel_num)
                        name_elem = elem.find(f"{namespace}channelName")
                        channel_name = (
                            name_elem.text
                            if name_elem is not None and name_elem.text
                            else f"Channel {channel_num}"
                        )
                        enabled_elem = elem.find(f"{namespace}enabled")
                        enabled = (
                            enabled_elem.text.lower() == "true"
                            if enabled_elem is not None and enabled_elem.text
                            else True
                        )
                        channels.append(
                            HikvisionChannel(
                                id=channel_num, name=channel_name, enabled=enabled
                            )
                        )
                except ValueError:
                    continue
        session.close()
        return channels

    # Process VideoInputChannel or InputProxyChannel elements
    for elem in channel_elements:
        channel_id_elem = elem.find(f"{namespace}id")
        if channel_id_elem is None or not channel_id_elem.text:
            continue

        try:
            channel_id = int(channel_id_elem.text)
        except ValueError:
            continue

        # Get channel name
        name_elem = elem.find(f"{namespace}name")
        if name_elem is None or not name_elem.text:
            name_elem = elem.find(f"{namespace}channelName")
        channel_name = (
            name_elem.text
            if name_elem is not None and name_elem.text
            else f"Channel {channel_id}"
        )

        # Check if channel is enabled (for InputProxyChannel)
        enabled = True
        enabled_elem = elem.find(f"{namespace}enabled")
        if enabled_elem is not None and enabled_elem.text:
            enabled = enabled_elem.text.lower() == "true"

        # For InputProxyChannel, also check online status
        online_elem = elem.find(f"{namespace}online")
        if online_elem is not None and online_elem.text:
            enabled = enabled and online_elem.text.lower() == "true"

        channels.append(
            HikvisionChannel(id=channel_id, name=channel_name, enabled=enabled)
        )

    session.close()
    return channels


def build_rtsp_url(
    host: str,
    port: int,
    username: str,
    password: str,
    channel: int,
    stream_type: int = 1,
) -> str:
    """Build RTSP URL for a Hikvision channel.

    Args:
        host: Device hostname or IP
        port: RTSP port (usually 554)
        username: Authentication username
        password: Authentication password
        channel: Channel number (1-based)
        stream_type: 1=main stream, 2=sub stream, 3=third stream

    Returns:
        RTSP URL string
    """
    # Channel ID format: (channel * 100) + stream_type
    channel_id = channel * 100 + stream_type
    return f"rtsp://{username}:{password}@{host}:{port}/Streaming/Channels/{channel_id}"


def build_snapshot_url(
    host: str,
    port: int,
    channel: int,
    ssl: bool = False,
    stream_type: int = 1,
) -> str:
    """Build snapshot URL for a Hikvision channel.

    Args:
        host: Device hostname or IP
        port: HTTP port
        channel: Channel number (1-based)
        ssl: Whether to use HTTPS
        stream_type: 1=main stream, 2=sub stream

    Returns:
        Snapshot URL string (without auth, added separately)
    """
    protocol = "https" if ssl else "http"
    channel_id = channel * 100 + stream_type
    return f"{protocol}://{host}:{port}/ISAPI/Streaming/channels/{channel_id}/picture"
