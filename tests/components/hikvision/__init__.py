"""Common test tools for the Hikvision integration."""

from unittest.mock import patch

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

# Default platforms to load in tests (excluding camera to avoid numpy dependency)
DEFAULT_TEST_PLATFORMS = [Platform.BINARY_SENSOR]


async def setup_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    platforms: list[Platform] | None = None,
) -> None:
    """Set up the Hikvision integration for testing.

    Args:
        hass: Home Assistant instance
        mock_config_entry: Mock config entry
        platforms: List of platforms to load (defaults to BINARY_SENSOR only)
    """
    if platforms is None:
        platforms = DEFAULT_TEST_PLATFORMS

    mock_config_entry.add_to_hass(hass)
    with patch("homeassistant.components.hikvision.PLATFORMS", platforms):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
