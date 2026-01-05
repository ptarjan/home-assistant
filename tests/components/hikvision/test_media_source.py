"""Tests for the Hikvision media_source platform."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from homeassistant.components.hikvision.const import DOMAIN
from homeassistant.components.media_source import (
    DOMAIN as MEDIA_SOURCE_DOMAIN,
    URI_SCHEME,
    Unresolvable,
    async_browse_media,
    async_resolve_media,
)
from homeassistant.components.stream import DOMAIN as MEDIA_STREAM_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from . import setup_integration
from .conftest import TEST_DEVICE_NAME

from tests.common import MockConfigEntry


@pytest.fixture(autouse=True)
async def setup_component(hass: HomeAssistant) -> None:
    """Set up media source and stream components."""
    assert await async_setup_component(hass, MEDIA_SOURCE_DOMAIN, {})
    assert await async_setup_component(hass, MEDIA_STREAM_DOMAIN, {})


@pytest.fixture
def mock_hikcamera_multi_channel(mock_hikcamera: MagicMock) -> MagicMock:
    """Return a mocked HikCamera with multiple channels."""
    camera = mock_hikcamera.return_value
    camera.get_channels = MagicMock(return_value=[1, 2])
    return mock_hikcamera


@pytest.fixture
def mock_hikcamera_with_days(mock_hikcamera: MagicMock) -> MagicMock:
    """Return a mocked HikCamera with recording days."""
    camera = mock_hikcamera.return_value

    class MockRecordingDay:
        def __init__(self, date: datetime) -> None:
            self.date = date
            self.has_recordings = True

    camera.get_recording_days = MagicMock(
        return_value=[
            MockRecordingDay(datetime(2024, 1, 15, 0, 0)),
            MockRecordingDay(datetime(2024, 1, 14, 0, 0)),
        ]
    )
    return mock_hikcamera


@pytest.fixture
def mock_hikcamera_with_recordings(mock_hikcamera_with_days: MagicMock) -> MagicMock:
    """Return a mocked HikCamera with recordings."""
    camera = mock_hikcamera_with_days.return_value

    class MockRecording:
        def __init__(
            self, start: datetime, end: datetime, track_id: int = 101
        ) -> None:
            self.source_id = f"source_{track_id}"
            self.track_id = track_id
            self.start_time = start
            self.end_time = end
            self.content_type = "video"
            self.playback_uri = f"rtsp://192.168.1.100/Streaming/tracks/{track_id}/?starttime={start.strftime('%Y%m%dT%H%M%S')}Z&endtime={end.strftime('%Y%m%dT%H%M%S')}Z"

    camera.search_recordings = MagicMock(
        return_value=[
            MockRecording(
                datetime(2024, 1, 15, 10, 30, 0),
                datetime(2024, 1, 15, 10, 35, 0),
            ),
        ]
    )
    return mock_hikcamera_with_days


async def test_browse_root(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
) -> None:
    """Test browsing the root of the media source."""
    await setup_integration(hass, mock_config_entry)

    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}")

    assert browse.domain == DOMAIN
    assert browse.title == "Hikvision"
    assert browse.identifier is None
    assert len(browse.children) == 1
    assert browse.children[0].identifier == f"DEVICE|{mock_config_entry.entry_id}"
    assert TEST_DEVICE_NAME in browse.children[0].title


async def test_browse_channels(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera_multi_channel: MagicMock,
) -> None:
    """Test browsing channels of a device."""
    await setup_integration(hass, mock_config_entry)

    device_id = f"DEVICE|{mock_config_entry.entry_id}"
    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{device_id}")

    assert browse.domain == DOMAIN
    assert browse.title == TEST_DEVICE_NAME
    assert len(browse.children) == 2
    assert "Channel 1" in browse.children[0].title
    assert "Channel 2" in browse.children[1].title


async def test_browse_recording_days(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera_with_days: MagicMock,
) -> None:
    """Test browsing recording days for a channel."""
    await setup_integration(hass, mock_config_entry)

    # Use channel 1 (becomes track 101 internally)
    channel_id = f"CHANNEL|{mock_config_entry.entry_id}|1"
    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{channel_id}")

    assert browse.domain == DOMAIN
    assert "Recordings" in browse.title
    assert len(browse.children) == 2
    assert "2024-01-15" in browse.children[0].title
    assert "2024-01-14" in browse.children[1].title


async def test_browse_recordings(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera_with_recordings: MagicMock,
) -> None:
    """Test browsing recordings for a specific day."""
    await setup_integration(hass, mock_config_entry)

    day_id = f"DAY|{mock_config_entry.entry_id}|1|2024|1|15"
    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{day_id}")

    assert browse.domain == DOMAIN
    assert "2024-01-15" in browse.title
    assert len(browse.children) == 1
    # Recordings are now expandable with time slots
    assert browse.children[0].can_expand is True
    assert "10:30:00" in browse.children[0].title


async def test_resolve_media(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
) -> None:
    """Test resolving media to a playable URL."""
    await setup_integration(hass, mock_config_entry)

    file_id = (
        f"FILE|{mock_config_entry.entry_id}|1|"
        f"20240115T103000Z|20240115T103500Z|"
        f"rtsp%3A%2F%2F192.168.1.100%2FStreaming%2Ftracks%2F101"
    )
    play_media = await async_resolve_media(
        hass, f"{URI_SCHEME}{DOMAIN}/{file_id}", None
    )

    assert play_media.mime_type == "application/x-mpegURL"
    assert "/api/hikvision/hls/" in play_media.url


async def test_browse_errors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
) -> None:
    """Test browsing errors."""
    await setup_integration(hass, mock_config_entry)

    # Test unknown identifier
    with pytest.raises(Unresolvable):
        await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/UNKNOWN")

    # Test resolve with invalid identifier
    with pytest.raises(Unresolvable):
        await async_resolve_media(hass, f"{URI_SCHEME}{DOMAIN}/UNKNOWN", None)


async def test_resolve_no_identifier(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
) -> None:
    """Test resolving media with no identifier."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(Unresolvable):
        await async_resolve_media(hass, f"{URI_SCHEME}{DOMAIN}/", None)


async def test_browse_nvr(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hik_nvr: MagicMock,
) -> None:
    """Test browsing an NVR device."""
    await setup_integration(hass, mock_config_entry)

    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}")

    assert browse.domain == DOMAIN
    assert browse.title == "Hikvision"
    assert len(browse.children) == 1
    # NVR should show in the title
    assert "NVR" in browse.children[0].title


async def test_resolve_without_playback_uri(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
) -> None:
    """Test resolving media without a playback URI constructs RTSP URL."""
    await setup_integration(hass, mock_config_entry)

    # No playback URI in the identifier
    file_id = f"FILE|{mock_config_entry.entry_id}|1|20240115T103000Z|20240115T103500Z|"
    play_media = await async_resolve_media(
        hass, f"{URI_SCHEME}{DOMAIN}/{file_id}", None
    )

    assert play_media.mime_type == "application/x-mpegURL"
    # Should use HLS endpoint
    assert "/api/hikvision/hls/" in play_media.url
