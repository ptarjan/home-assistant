"""Common fixtures for the Hikvision tests."""

from collections.abc import AsyncGenerator, Generator
from unittest.mock import MagicMock, patch

import pytest

from homeassistant.components.hikvision import PLATFORMS
from homeassistant.components.hikvision.const import DOMAIN
from homeassistant.components.hikvision.coordinator import HikvisionCoordinatorData
from homeassistant.components.hikvision.isapi import (
    AlarmServerInfo,
    CameraInfo,
    DeviceCapabilities,
    StreamInfo,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry, setup_integration

TEST_HOST = "192.168.1.100"
TEST_PORT = 80
TEST_USERNAME = "admin"
TEST_PASSWORD = "password123"
TEST_DEVICE_ID = "DS-2CD2142FWD-I20170101AAAA"
TEST_DEVICE_NAME = "Front Camera"
TEST_DEVICE_MODEL = "DS-2CD2142FWD-I"
TEST_FIRMWARE_VERSION = "V5.4.5"


@pytest.fixture
def platforms() -> list[Platform]:
    """Platforms, which should be loaded during the test."""
    return PLATFORMS


@pytest.fixture(autouse=True)
async def mock_patch_platforms(platforms: list[Platform]) -> AsyncGenerator[None]:
    """Fixture to set up platforms for tests."""
    with patch(f"homeassistant.components.{DOMAIN}.PLATFORMS", platforms):
        yield


@pytest.fixture
def mock_setup_entry() -> Generator[MagicMock]:
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
        minor_version=2,
        data={
            CONF_HOST: TEST_HOST,
            CONF_PORT: TEST_PORT,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_SSL: False,
        },
        unique_id=TEST_DEVICE_ID,
    )


def _create_mock_coordinator_data() -> HikvisionCoordinatorData:
    """Create mock coordinator data."""
    return HikvisionCoordinatorData(
        device_serial=TEST_DEVICE_ID,
        device_name=TEST_DEVICE_NAME,
        device_model=TEST_DEVICE_MODEL,
        device_type="Camera",
        capabilities=DeviceCapabilities(
            support_holiday_mode=True,
            support_alarm_server=True,
            support_io_outputs=False,
            support_storage=True,
            num_io_outputs=0,
        ),
        cameras=[
            CameraInfo(
                id=1,
                name="Channel 1",
                streams=[
                    StreamInfo(
                        id="101",
                        channel_id=1,
                        type_id=1,
                        name="Channel 1",
                        enabled=True,
                    ),
                    StreamInfo(
                        id="102",
                        channel_id=1,
                        type_id=2,
                        name="Channel 1 Sub",
                        enabled=True,
                    ),
                ],
            ),
        ],
        event_states={},
        output_ports=[],
        output_states={},
        storage_devices=[],
        alarm_server=AlarmServerInfo(
            protocol="HTTP",
            address="192.168.1.50",
            port=8080,
            path="/api/alerts",
        ),
        holiday_mode_enabled=False,
    )


@pytest.fixture
def mock_isapi_client() -> Generator[MagicMock]:
    """Return a mocked ISAPIClient."""
    with patch(
        "homeassistant.components.hikvision.ISAPIClient",
        autospec=True,
    ) as isapi_mock:
        client = isapi_mock.return_value
        client.get_device_info = AsyncMock(
            return_value={
                "serialNumber": TEST_DEVICE_ID,
                "deviceName": TEST_DEVICE_NAME,
                "model": TEST_DEVICE_MODEL,
                "deviceType": "Camera",
                "firmwareVersion": TEST_FIRMWARE_VERSION,
            }
        )
        client.get_device_serial = AsyncMock(return_value=TEST_DEVICE_ID)
        client.get_device_name = AsyncMock(return_value=TEST_DEVICE_NAME)
        client.get_device_model = AsyncMock(return_value=TEST_DEVICE_MODEL)
        client.get_device_type = AsyncMock(return_value="Camera")
        client.get_capabilities = AsyncMock(
            return_value=DeviceCapabilities(
                support_holiday_mode=True,
                support_alarm_server=True,
                support_io_outputs=False,
                support_storage=True,
                num_io_outputs=0,
            )
        )
        client.get_cameras = AsyncMock(
            return_value=[
                CameraInfo(
                    id=1,
                    name="Channel 1",
                    streams=[
                        StreamInfo(
                            id="101",
                            channel_id=1,
                            type_id=1,
                            name="Channel 1",
                            enabled=True,
                        ),
                    ],
                ),
            ]
        )
        client.get_streaming_channels = AsyncMock(
            return_value=[
                StreamInfo(
                    id="101",
                    channel_id=1,
                    type_id=1,
                    name="Channel 1",
                    enabled=True,
                ),
            ]
        )
        client.get_output_ports = AsyncMock(return_value=[])
        client.get_output_state = AsyncMock(return_value=False)
        client.get_storage_devices = AsyncMock(return_value=[])
        client.get_alarm_server_info = AsyncMock(
            return_value=AlarmServerInfo(
                protocol="HTTP",
                address="192.168.1.50",
                port=8080,
                path="/api/alerts",
            )
        )
        client.get_holiday_mode_enabled = AsyncMock(return_value=False)
        client.get_event_states = AsyncMock(return_value=[])
        client.get_snapshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
        client.get_rtsp_url = MagicMock(
            return_value="rtsp://admin:password123@192.168.1.100:554/Streaming/Channels/101"
        )
        client.reboot = AsyncMock()
        client.custom_request = AsyncMock(return_value={"status": "ok"})
        yield isapi_mock


@pytest.fixture
def mock_hikcamera(mock_isapi_client: MagicMock) -> Generator[MagicMock]:
    """Return a mocked HikCamera."""
    with (
        patch(
            "homeassistant.components.hikvision.HikCamera",
        ) as hikcamera_mock,
        patch(
            "homeassistant.components.hikvision.config_flow.HikCamera",
            new=hikcamera_mock,
        ),
    ):
        camera = hikcamera_mock.return_value
        camera.get_id = TEST_DEVICE_ID
        camera.get_name = TEST_DEVICE_NAME
        camera.get_type = "Camera"
        camera.current_event_states = {
            "Motion": [(True, 1)],
            "Line Crossing": [(False, 1)],
        }
        camera.fetch_attributes.return_value = (
            False,
            None,
            None,
            "2024-01-01T00:00:00Z",
        )
        camera.get_event_triggers.return_value = {}

        # pyHik 0.4.0 methods
        camera.get_channels.return_value = [1]
        camera.get_snapshot.return_value = b"fake_image_data"
        camera.get_stream_url.return_value = (
            f"rtsp://{TEST_USERNAME}:{TEST_PASSWORD}"
            f"@{TEST_HOST}:554/Streaming/Channels/1"
        )

        yield hikcamera_mock


@pytest.fixture
def mock_hik_nvr(mock_hikcamera: MagicMock) -> MagicMock:
    """Return a mocked HikCamera configured as an NVR."""
    camera = mock_hikcamera.return_value
    camera.get_type = "NVR"
    camera.current_event_states = {}
    camera.get_event_triggers.return_value = {"Motion": [1, 2]}
    return mock_hikcamera


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_isapi_client: MagicMock,
) -> MockConfigEntry:
    """Set up the Hikvision integration for testing."""
    await setup_integration(hass, mock_config_entry)
    return mock_config_entry
