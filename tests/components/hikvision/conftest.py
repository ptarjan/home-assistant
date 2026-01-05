"""Common fixtures for the Hikvision tests."""

from collections.abc import Generator
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.hikvision.const import DOMAIN
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant

from . import setup_integration

from tests.common import MockConfigEntry

TEST_HOST = "192.168.1.100"
TEST_PORT = 80
TEST_USERNAME = "admin"
TEST_PASSWORD = "password123"
TEST_DEVICE_ID = "DS-2CD2142FWD-I20170101AAAA"
TEST_DEVICE_NAME = "Front Camera"


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "homeassistant.components.hikvision.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return the default mocked config entry."""
    return MockConfigEntry(
        title=TEST_DEVICE_NAME,
        domain=DOMAIN,
        version=1,
        minor_version=1,
        data={
            CONF_HOST: TEST_HOST,
            CONF_PORT: TEST_PORT,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_SSL: False,
        },
        unique_id=TEST_DEVICE_ID,
    )


@pytest.fixture
def mock_hikcamera() -> Generator[MagicMock]:
    """Return a mocked HikCamera with pyHik 0.4.0 methods."""
    with patch(
        "homeassistant.components.hikvision.HikCamera",
        autospec=True,
    ) as hikcamera_mock:
        camera = hikcamera_mock.return_value
        camera.get_id = TEST_DEVICE_ID
        camera.get_name = TEST_DEVICE_NAME
        camera.get_type = "Camera"
        camera.root_url = f"http://{TEST_HOST}:{TEST_PORT}"
        camera.usr = TEST_USERNAME
        camera.pwd = TEST_PASSWORD
        camera.current_event_states = {
            "Motion": [(True, 1)],
            "Line Crossing": [(False, 1)],
        }
        camera.start_stream = MagicMock()
        camera.disconnect = MagicMock()
        camera.add_update_callback = MagicMock()
        camera.fetch_attributes = MagicMock(
            return_value=(False, None, None, "2024-01-01T00:00:00Z")
        )
        camera.get_event_triggers = MagicMock(return_value={})
        camera.inject_events = MagicMock()

        # pyHik 0.4.0 methods
        camera.get_channels = MagicMock(return_value=[1])
        camera.get_stream_url = MagicMock(
            return_value=f"rtsp://{TEST_USERNAME}:{TEST_PASSWORD}@{TEST_HOST}:554/Streaming/Channels/101"
        )
        camera.get_snapshot = MagicMock(return_value=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        camera.get_recording_days = MagicMock(return_value=[])
        camera.search_recordings = MagicMock(return_value=[])

        yield hikcamera_mock


@pytest.fixture
def mock_hik_nvr(mock_hikcamera: MagicMock) -> MagicMock:
    """Return a mocked HikCamera configured as an NVR."""
    camera = mock_hikcamera.return_value
    camera.get_type = "NVR"
    camera.current_event_states = {}
    camera.get_event_triggers = MagicMock(return_value={"Motion": [1, 2]})
    camera.get_channels = MagicMock(return_value=[1, 2, 3])
    return mock_hikcamera


@pytest.fixture
def mock_hikcamera_config_flow() -> Generator[MagicMock]:
    """Return a mocked HikCamera for config flow."""
    with patch(
        "homeassistant.components.hikvision.config_flow.HikCamera",
    ) as hikcamera_mock:
        camera = hikcamera_mock.return_value
        camera.get_id.return_value = TEST_DEVICE_ID
        camera.get_name = TEST_DEVICE_NAME
        camera.get_type = "Camera"
        yield hikcamera_mock


@pytest.fixture
def mock_hikcamera_with_recordings(mock_hikcamera: MagicMock) -> MagicMock:
    """Return a mocked HikCamera with recording data."""
    camera = mock_hikcamera.return_value

    # Create mock RecordingDay objects
    class MockRecordingDay:
        def __init__(self, date: datetime) -> None:
            self.date = date
            self.has_recordings = True

    # Create mock Recording objects
    class MockRecording:
        def __init__(
            self, start: datetime, end: datetime, track_id: int = 101
        ) -> None:
            self.source_id = f"source_{track_id}"
            self.track_id = track_id
            self.start_time = start
            self.end_time = end
            self.content_type = "video"
            self.playback_uri = f"rtsp://{TEST_HOST}:554/Streaming/tracks/{track_id}/?starttime={start.strftime('%Y%m%dT%H%M%S')}Z&endtime={end.strftime('%Y%m%dT%H%M%S')}Z"

    # Set up recording days
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    camera.get_recording_days = MagicMock(
        return_value=[
            MockRecordingDay(today),
        ]
    )

    # Set up recordings
    recording_start = today.replace(hour=10, minute=0, second=0)
    recording_end = today.replace(hour=10, minute=30, second=0)
    camera.search_recordings = MagicMock(
        return_value=[
            MockRecording(recording_start, recording_end),
        ]
    )

    return mock_hikcamera


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
) -> MockConfigEntry:
    """Set up the Hikvision integration for testing."""
    await setup_integration(hass, mock_config_entry)
    return mock_config_entry
