"""Tests for the Hikvision ISAPI client."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from homeassistant.components.hikvision.isapi import HikvisionISAPIClient, Recording


@pytest.fixture
def mock_camera() -> MagicMock:
    """Return a mocked HikCamera."""
    camera = MagicMock()
    camera.root_url = "http://192.168.1.100"
    camera.port = 80
    camera.usr = "admin"
    camera.pwd = "password"
    camera.get_name = "Test Camera"
    return camera


@pytest.fixture
def isapi_client(mock_camera: MagicMock) -> HikvisionISAPIClient:
    """Return an ISAPI client with mocked camera."""
    return HikvisionISAPIClient(mock_camera)


def test_client_initialization(mock_camera: MagicMock) -> None:
    """Test ISAPI client initialization."""
    client = HikvisionISAPIClient(mock_camera)
    assert client._base_url == "http://192.168.1.100:80"


def test_get_channels_single_camera(
    isapi_client: HikvisionISAPIClient, mock_camera: MagicMock
) -> None:
    """Test getting channels for a single camera device."""
    # When no channels are returned via API, fall back to single channel
    with patch.object(isapi_client._session, "get") as mock_get:
        mock_get.return_value.status_code = 404
        channels = isapi_client.get_channels()

    assert len(channels) == 1
    assert channels[0]["id"] == "101"


def test_get_channels_from_input_proxy(
    isapi_client: HikvisionISAPIClient,
) -> None:
    """Test getting channels from InputProxy API."""
    xml_response = b"""<?xml version="1.0" encoding="UTF-8"?>
    <InputProxyChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
        <InputProxyChannel>
            <id>1</id>
            <name>Channel 1</name>
        </InputProxyChannel>
        <InputProxyChannel>
            <id>2</id>
            <name>Channel 2</name>
        </InputProxyChannel>
    </InputProxyChannelList>"""

    with patch.object(isapi_client._session, "get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_response
        mock_get.return_value = mock_response

        channels = isapi_client.get_channels()

    assert len(channels) == 2
    assert channels[0]["id"] == "1"
    assert channels[0]["name"] == "Channel 1"


def test_get_channels_from_streaming(
    isapi_client: HikvisionISAPIClient,
) -> None:
    """Test getting channels from Streaming API when InputProxy fails."""
    streaming_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <StreamingChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
        <StreamingChannel>
            <id>101</id>
            <channelName>Front Door</channelName>
        </StreamingChannel>
        <StreamingChannel>
            <id>102</id>
            <channelName>Front Door Sub</channelName>
        </StreamingChannel>
        <StreamingChannel>
            <id>201</id>
            <channelName>Backyard</channelName>
        </StreamingChannel>
    </StreamingChannelList>"""

    with patch.object(isapi_client._session, "get") as mock_get:

        def mock_response(url, **kwargs):
            resp = MagicMock()
            if "InputProxy" in url:
                resp.status_code = 404
            else:
                resp.status_code = 200
                resp.content = streaming_xml
            return resp

        mock_get.side_effect = mock_response
        channels = isapi_client.get_channels()

    # Should only return main streams (ending in 01)
    assert len(channels) == 2
    assert channels[0]["id"] == "101"
    assert channels[0]["name"] == "Front Door"
    assert channels[1]["id"] == "201"
    assert channels[1]["name"] == "Backyard"


def test_get_recording_days(isapi_client: HikvisionISAPIClient) -> None:
    """Test getting days with recordings."""
    xml_response = b"""<?xml version="1.0" encoding="UTF-8"?>
    <CMSearchResult xmlns="http://www.hikvision.com/ver20/XMLSchema">
        <searchMatchItem>
            <timeSpan>
                <startTime>2024-01-15T10:30:00Z</startTime>
                <endTime>2024-01-15T10:35:00Z</endTime>
            </timeSpan>
        </searchMatchItem>
        <searchMatchItem>
            <timeSpan>
                <startTime>2024-01-14T09:00:00Z</startTime>
                <endTime>2024-01-14T09:15:00Z</endTime>
            </timeSpan>
        </searchMatchItem>
    </CMSearchResult>"""

    with patch.object(isapi_client._session, "post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_response
        mock_post.return_value = mock_response

        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 31)
        days = isapi_client.get_recording_days(101, start_date, end_date)

    assert len(days) == 2
    assert days[0].date.day == 15  # Most recent first
    assert days[1].date.day == 14


def test_search_recordings(isapi_client: HikvisionISAPIClient) -> None:
    """Test searching for recordings."""
    xml_response = b"""<?xml version="1.0" encoding="UTF-8"?>
    <CMSearchResult xmlns="http://www.hikvision.com/ver20/XMLSchema">
        <searchMatchItem>
            <sourceID>1</sourceID>
            <trackID>101</trackID>
            <timeSpan>
                <startTime>2024-01-15T10:30:00Z</startTime>
                <endTime>2024-01-15T10:35:00Z</endTime>
            </timeSpan>
            <mediaSegmentDescriptor>
                <contentType>video</contentType>
                <playbackURI>rtsp://192.168.1.100/test</playbackURI>
            </mediaSegmentDescriptor>
        </searchMatchItem>
    </CMSearchResult>"""

    with patch.object(isapi_client._session, "post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_response
        mock_post.return_value = mock_response

        start_time = datetime(2024, 1, 15, 0, 0)
        end_time = datetime(2024, 1, 15, 23, 59)
        recordings = isapi_client.search_recordings(101, start_time, end_time)

    assert len(recordings) == 1
    assert recordings[0].source_id == "1"
    assert recordings[0].track_id == 101
    assert recordings[0].playback_uri == "rtsp://192.168.1.100/test"


def test_get_playback_url_with_rtsp(isapi_client: HikvisionISAPIClient) -> None:
    """Test getting playback URL when RTSP URI is provided."""
    recording = Recording(
        source_id="1",
        track_id=101,
        start_time=datetime(2024, 1, 15, 10, 30),
        end_time=datetime(2024, 1, 15, 10, 35),
        content_type="video",
        playback_uri="rtsp://192.168.1.100/Streaming/tracks/101",
    )

    url = isapi_client.get_playback_url(recording)

    assert "rtsp://" in url
    assert "admin:password@" in url
    assert "192.168.1.100" in url


def test_get_playback_url_constructed(isapi_client: HikvisionISAPIClient) -> None:
    """Test getting playback URL when no URI is provided."""
    recording = Recording(
        source_id="1",
        track_id=101,
        start_time=datetime(2024, 1, 15, 10, 30),
        end_time=datetime(2024, 1, 15, 10, 35),
        content_type="video",
        playback_uri="",
    )

    url = isapi_client.get_playback_url(recording)

    assert "rtsp://admin:password@192.168.1.100:554" in url
    assert "tracks/101" in url
    assert "starttime=" in url
    assert "endtime=" in url


def test_get_channels_network_error(
    isapi_client: HikvisionISAPIClient, mock_camera: MagicMock
) -> None:
    """Test handling network errors when getting channels."""
    with patch.object(isapi_client._session, "get") as mock_get:
        mock_get.side_effect = requests.RequestException("Connection failed")
        channels = isapi_client.get_channels()

    # Should fall back to single channel
    assert len(channels) == 1
    assert channels[0]["id"] == "101"


def test_search_recordings_network_error(
    isapi_client: HikvisionISAPIClient,
) -> None:
    """Test handling network errors when searching recordings."""
    with patch.object(isapi_client._session, "post") as mock_post:
        mock_post.side_effect = requests.RequestException("Connection failed")

        start_time = datetime(2024, 1, 15, 0, 0)
        end_time = datetime(2024, 1, 15, 23, 59)
        recordings = isapi_client.search_recordings(101, start_time, end_time)

    assert recordings == []


def test_get_recording_days_empty_response(
    isapi_client: HikvisionISAPIClient,
) -> None:
    """Test getting recording days with empty response."""
    xml_response = b"""<?xml version="1.0" encoding="UTF-8"?>
    <CMSearchResult xmlns="http://www.hikvision.com/ver20/XMLSchema">
    </CMSearchResult>"""

    with patch.object(isapi_client._session, "post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = xml_response
        mock_post.return_value = mock_response

        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 31)
        days = isapi_client.get_recording_days(101, start_date, end_date)

    assert days == []
