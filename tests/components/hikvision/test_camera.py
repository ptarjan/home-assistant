"""Tests for the Hikvision camera platform."""

from unittest.mock import MagicMock

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
    platforms: list[Platform],
) -> None:
    """Test camera setup with a single channel."""
    await setup_integration(hass, mock_config_entry, platforms)

    assert mock_config_entry.state is ConfigEntryState.LOADED

    # Verify camera entity was created (single camera uses device name)
    state = hass.states.get("camera.front_camera")
    assert state is not None


async def test_camera_setup_nvr_multiple_channels(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hik_nvr: MagicMock,
    platforms: list[Platform],
) -> None:
    """Test camera setup with NVR and multiple channels."""
    await setup_integration(hass, mock_config_entry, platforms)

    assert mock_config_entry.state is ConfigEntryState.LOADED

    # Verify camera entities were created for all channels
    entity_registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    camera_entities = [e for e in entities if e.domain == "camera"]

    # Should have 3 cameras (get_channels returns [1, 2, 3] for NVR)
    assert len(camera_entities) == 3


async def test_camera_stream_source(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    platforms: list[Platform],
) -> None:
    """Test camera stream source URL."""
    await setup_integration(hass, mock_config_entry, platforms)

    # Get the camera entity
    entity_id = "camera.front_camera"
    state = hass.states.get(entity_id)
    assert state is not None

    # Get the camera component
    camera_component = hass.data["camera"]
    camera = camera_component.get_entity(entity_id)
    assert camera is not None

    # Verify stream source uses pyHik's get_stream_url
    stream_source = await camera.stream_source()
    assert stream_source is not None
    # pyHik returns RTSP URL with credentials
    assert "rtsp://" in stream_source
    assert TEST_USERNAME in stream_source
    assert TEST_PASSWORD in stream_source


async def test_camera_snapshot(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    platforms: list[Platform],
) -> None:
    """Test camera snapshot fetching via pyHik."""
    await setup_integration(hass, mock_config_entry, platforms)

    entity_id = "camera.front_camera"
    camera_component = hass.data["camera"]
    camera = camera_component.get_entity(entity_id)

    # The mock_hikcamera already has get_snapshot returning fake image data
    image = await camera.async_camera_image()
    assert image is not None
    # Verify it's the PNG header we set in the mock
    assert image.startswith(b"\x89PNG\r\n\x1a\n")


async def test_camera_snapshot_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    platforms: list[Platform],
) -> None:
    """Test camera snapshot error handling."""
    # Configure the mock to raise an exception
    mock_hikcamera.return_value.get_snapshot = MagicMock(
        side_effect=Exception("Connection failed")
    )

    await setup_integration(hass, mock_config_entry, platforms)

    entity_id = "camera.front_camera"
    camera_component = hass.data["camera"]
    camera = camera_component.get_entity(entity_id)

    # Should return None on error
    image = await camera.async_camera_image()
    assert image is None


async def test_camera_extra_attributes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    platforms: list[Platform],
) -> None:
    """Test camera extra state attributes."""
    await setup_integration(hass, mock_config_entry, platforms)

    entity_id = "camera.front_camera"
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.attributes.get("channel_id") == 1
    assert state.attributes.get("channel_name") == "Channel 1"


async def test_camera_no_channels(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hikcamera: MagicMock,
    platforms: list[Platform],
) -> None:
    """Test camera setup when no channels are found."""
    # Configure the mock to return no channels
    mock_hikcamera.return_value.get_channels = MagicMock(return_value=[])

    await setup_integration(hass, mock_config_entry, platforms)

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
    entity_registry: er.EntityRegistry,
    platforms: list[Platform],
) -> None:
    """Test camera unique ID format."""
    await setup_integration(hass, mock_config_entry, platforms)

    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    camera_entities = [e for e in entities if e.domain == "camera"]

    assert len(camera_entities) == 1
    assert camera_entities[0].unique_id == f"{TEST_DEVICE_ID}_camera_1"


async def test_camera_nvr_naming(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hik_nvr: MagicMock,
    entity_registry: er.EntityRegistry,
    platforms: list[Platform],
) -> None:
    """Test camera naming for NVR with multiple channels."""
    await setup_integration(hass, mock_config_entry, platforms)

    # Get camera entities
    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    camera_entities = [e for e in entities if e.domain == "camera"]

    # Verify names include channel names
    names = {e.original_name for e in camera_entities}
    assert "Channel 1" in names
    assert "Channel 2" in names
    assert "Channel 3" in names
