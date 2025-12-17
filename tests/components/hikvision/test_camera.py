"""Tests for the Hikvision camera platform."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

# Camera tests require numpy for the stream component
# Skip all tests in this module if numpy is not available
pytest.importorskip("numpy", reason="Camera tests require numpy for stream component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import TEST_DEVICE_ID, TEST_HOST, TEST_PASSWORD, TEST_USERNAME

from tests.common import MockConfigEntry


@pytest.fixture
def platforms() -> list[Platform]:
    """Return the platforms to test."""
    return [Platform.CAMERA]


async def test_camera_setup_single_channel(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_get_nvr_events: MagicMock,
    mock_inject_events: MagicMock,
    mock_get_video_channels: MagicMock,
) -> None:
    """Test camera setup with a single channel."""
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED

    # Verify camera entity was created (single camera uses device name)
    state = hass.states.get("camera.front_camera")
    assert state is not None


async def test_camera_setup_nvr_multiple_channels(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_get_nvr_events: MagicMock,
    mock_inject_events: MagicMock,
    mock_get_video_channels_nvr: MagicMock,
) -> None:
    """Test camera setup with NVR and multiple channels."""
    # Set device type to NVR
    mock_hikcamera.return_value.get_type = "NVR"

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED

    # Verify camera entities were created for enabled channels only
    entity_registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    camera_entities = [e for e in entities if e.domain == "camera"]

    # Should have 3 cameras (channel 4 is disabled)
    assert len(camera_entities) == 3


async def test_camera_stream_source(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_get_nvr_events: MagicMock,
    mock_inject_events: MagicMock,
    mock_get_video_channels: MagicMock,
) -> None:
    """Test camera stream source URL."""
    await setup_integration(hass, mock_config_entry)

    # Get the camera entity
    entity_id = "camera.front_camera"
    state = hass.states.get(entity_id)
    assert state is not None

    # Get the camera component
    camera_component = hass.data["camera"]
    camera = camera_component.get_entity(entity_id)
    assert camera is not None

    # Verify stream source
    stream_source = await camera.stream_source()
    assert stream_source is not None
    assert f"rtsp://{TEST_USERNAME}:{TEST_PASSWORD}@{TEST_HOST}:554" in stream_source
    assert "/Streaming/Channels/101" in stream_source


async def test_camera_snapshot(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_get_nvr_events: MagicMock,
    mock_inject_events: MagicMock,
    mock_get_video_channels: MagicMock,
) -> None:
    """Test camera snapshot fetching."""
    await setup_integration(hass, mock_config_entry)

    entity_id = "camera.front_camera"
    camera_component = hass.data["camera"]
    camera = camera_component.get_entity(entity_id)

    # Mock the httpx client
    mock_image = b"fake_image_data"
    with patch(
        "homeassistant.components.hikvision.camera.get_async_client"
    ) as mock_client:
        mock_response = MagicMock()
        mock_response.content = mock_image
        mock_response.raise_for_status = MagicMock()
        mock_client.return_value.get = MagicMock(return_value=mock_response)

        # Make it awaitable
        async def mock_get(*args, **kwargs):
            return mock_response

        mock_client.return_value.get = mock_get

        image = await camera.async_camera_image()
        assert image == mock_image


async def test_camera_snapshot_timeout(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_get_nvr_events: MagicMock,
    mock_inject_events: MagicMock,
    mock_get_video_channels: MagicMock,
) -> None:
    """Test camera snapshot timeout handling."""
    await setup_integration(hass, mock_config_entry)

    entity_id = "camera.front_camera"
    camera_component = hass.data["camera"]
    camera = camera_component.get_entity(entity_id)

    with patch(
        "homeassistant.components.hikvision.camera.get_async_client"
    ) as mock_client:

        async def mock_get(*args, **kwargs):
            raise httpx.TimeoutException("Timeout")

        mock_client.return_value.get = mock_get

        # Should return None (last_image) on timeout
        image = await camera.async_camera_image()
        assert image is None


async def test_camera_extra_attributes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_get_nvr_events: MagicMock,
    mock_inject_events: MagicMock,
    mock_get_video_channels: MagicMock,
) -> None:
    """Test camera extra state attributes."""
    await setup_integration(hass, mock_config_entry)

    entity_id = "camera.front_camera"
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.attributes.get("channel_id") == 1
    assert state.attributes.get("channel_name") == "Front Camera"


async def test_camera_no_channels(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_get_nvr_events: MagicMock,
    mock_inject_events: MagicMock,
) -> None:
    """Test camera setup when no channels are found."""
    with patch(
        "homeassistant.components.hikvision.get_video_channels",
        return_value=[],
    ):
        await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED

    # Verify no camera entities were created
    entity_registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    camera_entities = [e for e in entities if e.domain == "camera"]
    assert len(camera_entities) == 0


async def test_camera_unique_id(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_get_nvr_events: MagicMock,
    mock_inject_events: MagicMock,
    mock_get_video_channels: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test camera unique ID format."""
    await setup_integration(hass, mock_config_entry)

    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    camera_entities = [e for e in entities if e.domain == "camera"]

    assert len(camera_entities) == 1
    assert camera_entities[0].unique_id == f"{TEST_DEVICE_ID}_camera_1"


async def test_camera_nvr_naming(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    mock_get_nvr_events: MagicMock,
    mock_inject_events: MagicMock,
    mock_get_video_channels_nvr: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test camera naming for NVR with multiple channels."""
    mock_hikcamera.return_value.get_type = "NVR"

    await setup_integration(hass, mock_config_entry)

    # Get camera entities
    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    camera_entities = [e for e in entities if e.domain == "camera"]

    # Verify names include channel names
    names = {e.original_name for e in camera_entities}
    assert "Front Door" in names
    assert "Backyard" in names
    assert "Garage" in names
