"""Tests for the Hikvision media_source platform."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from homeassistant.components.hikvision.const import DOMAIN
from homeassistant.components.hikvision.isapi import Recording, RecordingDay
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
def mock_isapi_client() -> MagicMock:
    """Return a mocked HikvisionISAPIClient."""
    client = MagicMock()
    client.get_channels.return_value = [
        {"id": "101", "name": "Front Door"},
        {"id": "201", "name": "Backyard"},
    ]
    client.get_recording_days.return_value = [
        RecordingDay(date=datetime(2024, 1, 15, 0, 0), has_recordings=True),
        RecordingDay(date=datetime(2024, 1, 14, 0, 0), has_recordings=True),
    ]
    client.search_recordings.return_value = [
        Recording(
            source_id="1",
            track_id=101,
            start_time=datetime(2024, 1, 15, 10, 30, 0),
            end_time=datetime(2024, 1, 15, 10, 35, 0),
            content_type="video",
            playback_uri="rtsp://192.168.1.100/Streaming/tracks/101/?starttime=20240115T103000Z&endtime=20240115T103500Z",
        ),
    ]
    return client


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
    mock_hikcamera: MagicMock,
    mock_isapi_client: MagicMock,
) -> None:
    """Test browsing channels of a device."""
    await setup_integration(hass, mock_config_entry)

    with patch(
        "homeassistant.components.hikvision.media_source.HikvisionISAPIClient",
        return_value=mock_isapi_client,
    ):
        device_id = f"DEVICE|{mock_config_entry.entry_id}"
        browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{device_id}")

    assert browse.domain == DOMAIN
    assert browse.title == TEST_DEVICE_NAME
    assert len(browse.children) == 2
    assert "Front Door" in browse.children[0].title
    assert "Backyard" in browse.children[1].title


async def test_browse_recording_days(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_isapi_client: MagicMock,
) -> None:
    """Test browsing recording days for a channel."""
    await setup_integration(hass, mock_config_entry)

    with patch(
        "homeassistant.components.hikvision.media_source.HikvisionISAPIClient",
        return_value=mock_isapi_client,
    ):
        channel_id = f"CHANNEL|{mock_config_entry.entry_id}|101"
        browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{channel_id}")

    assert browse.domain == DOMAIN
    assert "Recordings" in browse.title
    assert len(browse.children) == 2
    assert "2024-01-15" in browse.children[0].title
    assert "2024-01-14" in browse.children[1].title


async def test_browse_recordings(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_isapi_client: MagicMock,
) -> None:
    """Test browsing recordings for a specific day."""
    await setup_integration(hass, mock_config_entry)

    with patch(
        "homeassistant.components.hikvision.media_source.HikvisionISAPIClient",
        return_value=mock_isapi_client,
    ):
        day_id = f"DAY|{mock_config_entry.entry_id}|101|2024|1|15"
        browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{day_id}")

    assert browse.domain == DOMAIN
    assert "2024-01-15" in browse.title
    assert len(browse.children) == 1
    assert browse.children[0].can_play is True
    assert "10:30:00" in browse.children[0].title


async def test_resolve_media(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
) -> None:
    """Test resolving media to a playable URL."""
    await setup_integration(hass, mock_config_entry)

    # Mock the stream component
    with patch(
        "homeassistant.components.hikvision.media_source.create_stream"
    ) as mock_stream:
        mock_stream_instance = MagicMock()
        mock_stream_instance.endpoint_url.return_value = "http://localhost/stream.m3u8"
        mock_stream.return_value = mock_stream_instance

        file_id = (
            f"FILE|{mock_config_entry.entry_id}|101|"
            f"20240115T103000Z|20240115T103500Z|"
            f"rtsp%3A%2F%2F192.168.1.100%2FStreaming%2Ftracks%2F101"
        )
        play_media = await async_resolve_media(
            hass, f"{URI_SCHEME}{DOMAIN}/{file_id}", None
        )

    assert play_media.mime_type == "application/x-mpegURL"
    assert "stream" in play_media.url


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
    mock_isapi_client: MagicMock,
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
    mock_hikcamera.return_value.usr = "admin"
    mock_hikcamera.return_value.pwd = "password"
    mock_hikcamera.return_value.root_url = "http://192.168.1.100"

    await setup_integration(hass, mock_config_entry)

    with patch(
        "homeassistant.components.hikvision.media_source.create_stream"
    ) as mock_stream:
        mock_stream_instance = MagicMock()
        mock_stream_instance.endpoint_url.return_value = "http://localhost/stream.m3u8"
        mock_stream.return_value = mock_stream_instance

        # No playback URI in the identifier
        file_id = f"FILE|{mock_config_entry.entry_id}|101|20240115T103000Z|20240115T103500Z|"
        play_media = await async_resolve_media(
            hass, f"{URI_SCHEME}{DOMAIN}/{file_id}", None
        )

    assert play_media.mime_type == "application/x-mpegURL"
    # Verify the stream was created with an RTSP URL
    mock_stream.assert_called_once()
    rtsp_url = mock_stream.call_args[0][1]
    assert "rtsp://" in rtsp_url
    assert "admin:password" in rtsp_url
