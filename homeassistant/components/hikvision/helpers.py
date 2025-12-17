"""Helper functions for Hikvision integration."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import logging
from typing import TYPE_CHECKING

import defusedxml.ElementTree as ET
import requests
from requests.auth import HTTPDigestAuth

if TYPE_CHECKING:
    from pyhik.hikvision import HikCamera

_LOGGER = logging.getLogger(__name__)


@dataclass
class HikvisionChannel:
    """Represents a video input channel on a Hikvision device."""

    id: int
    name: str
    enabled: bool = True


# Event type mapping (same as pyhik SENSOR_MAP)
SENSOR_MAP = {
    "vmd": "Motion",
    "linedetection": "Line Crossing",
    "fielddetection": "Field Detection",
    "videoloss": "Video Loss",
    "tamperdetection": "Tamper Detection",
    "shelteralarm": "Tamper Detection",
    "defocus": "Tamper Detection",
    "diskfull": "Disk Full",
    "diskerror": "Disk Error",
    "nicbroken": "Net Interface Broken",
    "ipconflict": "IP Conflict",
    "illaccess": "Illegal Access",
    "videomismatch": "Video Mismatch",
    "badvideo": "Bad Video",
    "pir": "PIR Alarm",
    "facedetection": "Face Detection",
    "scenechangedetection": "Scene Change Detection",
    "io": "I/O",
    "unattendedbaggage": "Unattended Baggage",
    "attendedbaggage": "Attended Baggage",
    "recordingfailure": "Recording Failure",
    "regionexiting": "Exiting Region",
    "regionentrance": "Entering Region",
}

# Channel name attributes to look for
CHANNEL_NAMES = [
    "dynVideoInputChannelID",
    "videoInputChannelID",
    "dynInputIOPortID",
    "inputIOPortID",
    "id",
]

# Notification methods that indicate the event is active/configured
# Expanded from pyhik's limited list of just 'center' and 'HTTP'
VALID_NOTIFICATION_METHODS = {"center", "HTTP", "record", "email", "beep"}


def get_nvr_events(
    host: str,
    port: int,
    username: str,
    password: str,
    ssl: bool = False,
) -> dict[str, list[int]]:
    """Fetch events from NVR with broader notification method support.

    This function extends pyhik's event detection by also accepting
    'record', 'email', and 'beep' notification methods, which are commonly
    used on NVRs but ignored by pyhik.

    Returns a dict mapping event type names to lists of channel numbers.
    """
    protocol = "https" if ssl else "http"
    root_url = f"{protocol}://{host}:{port}"
    events: dict[str, list[int]] = {}

    session = requests.Session()
    session.auth = HTTPDigestAuth(username, password)

    urls = [
        f"{root_url}/ISAPI/Event/triggers",
        f"{root_url}/Event/triggers",
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
        _LOGGER.warning("Unable to fetch event triggers from NVR")
        return events

    try:
        tree = ET.fromstring(response.text)
    except ET.ParseError as err:
        _LOGGER.error("Failed to parse event triggers XML: %s", err)
        return events

    # Find all EventTrigger elements (handle namespaces)
    namespace = ""
    root_tag = tree.tag
    if root_tag.startswith("{"):
        namespace = root_tag.split("}")[0] + "}"

    # Try different XML structures (camera vs NVR)
    event_triggers = tree.findall(f".//{namespace}EventTrigger")

    for trigger in event_triggers:
        # Get event type
        event_type_elem = trigger.find(f"{namespace}eventType")
        if event_type_elem is None or not event_type_elem.text:
            continue

        event_type = event_type_elem.text.lower()

        # Skip videoloss as pyhik uses it for watchdog
        if event_type == "videoloss":
            continue

        # Get channel number
        channel_num = 0
        for channel_name in CHANNEL_NAMES:
            channel_elem = trigger.find(f"{namespace}{channel_name}")
            if channel_elem is not None and channel_elem.text:
                try:
                    channel_num = int(channel_elem.text)
                    break
                except ValueError:
                    continue

        # Check if any valid notification method is configured
        notification_list = trigger.find(f"{namespace}EventTriggerNotificationList")
        has_valid_notification = False

        if notification_list is not None:
            for notification in notification_list:
                method_elem = notification.find(f"{namespace}notificationMethod")
                if method_elem is not None and method_elem.text:
                    if method_elem.text.lower() in VALID_NOTIFICATION_METHODS:
                        has_valid_notification = True
                        break

        if has_valid_notification:
            # Map to friendly name
            friendly_name = SENSOR_MAP.get(event_type)
            if friendly_name:
                events.setdefault(friendly_name, []).append(channel_num)

    session.close()
    return events


def inject_events_into_camera(camera: HikCamera, events: dict[str, list[int]]) -> None:
    """Inject discovered events into the pyhik camera's event_states.

    This allows the camera to track events that pyhik wouldn't normally detect.
    """
    for event_name, channels in events.items():
        for channel in channels:
            # Only add if not already present
            if event_name not in camera.event_states:
                camera.event_states[event_name] = []

            # Check if this channel is already tracked
            channel_exists = any(
                sensor[1] == channel for sensor in camera.event_states[event_name]
            )
            if not channel_exists:
                # Add the event state: [is_active, channel, count, last_update_time]
                camera.event_states[event_name].append(
                    [False, channel, 0, datetime.datetime.now()]
                )


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
