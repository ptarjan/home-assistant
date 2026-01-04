"""Services for Hikvision integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .isapi import ISAPIAuthError, ISAPIConnectionError, ISAPIError

_LOGGER = logging.getLogger(__name__)

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_METHOD = "method"
ATTR_ENDPOINT = "endpoint"
ATTR_PAYLOAD = "payload"

SERVICE_REBOOT = "reboot"
SERVICE_ISAPI_REQUEST = "isapi_request"

SERVICE_REBOOT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)

SERVICE_ISAPI_REQUEST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_METHOD): vol.In(["GET", "PUT", "POST"]),
        vol.Required(ATTR_ENDPOINT): cv.string,
        vol.Optional(ATTR_PAYLOAD): cv.string,
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up Hikvision services."""

    async def _get_runtime_data(call: ServiceCall) -> Any:
        """Get runtime data from config entry."""
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        entry = hass.config_entries.async_get_entry(entry_id)

        if entry is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="config_entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )

        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="config_entry_not_loaded",
                translation_placeholders={"entry_id": entry_id},
            )

        return entry.runtime_data

    async def handle_reboot(call: ServiceCall) -> None:
        """Handle the reboot service call."""
        runtime_data = await _get_runtime_data(call)

        try:
            await runtime_data.coordinator.isapi_client.reboot()
        except ISAPIAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="reboot_permission_denied",
            ) from err
        except ISAPIConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="connection_failed",
            ) from err
        except ISAPIError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="reboot_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def handle_isapi_request(call: ServiceCall) -> ServiceResponse:
        """Handle the ISAPI request service call."""
        runtime_data = await _get_runtime_data(call)

        method = call.data[ATTR_METHOD]
        endpoint = call.data[ATTR_ENDPOINT]
        payload = call.data.get(ATTR_PAYLOAD)

        # Ensure endpoint starts with /
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"

        try:
            result = await runtime_data.coordinator.isapi_client.custom_request(
                method, endpoint, payload
            )
        except ISAPIAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="isapi_permission_denied",
            ) from err
        except ISAPIConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="connection_failed",
            ) from err
        except ISAPIError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="isapi_request_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        else:
            return {"result": result}

    hass.services.async_register(
        DOMAIN,
        SERVICE_REBOOT,
        handle_reboot,
        schema=SERVICE_REBOOT_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ISAPI_REQUEST,
        handle_isapi_request,
        schema=SERVICE_ISAPI_REQUEST_SCHEMA,
        supports_response=True,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Hikvision services."""
    hass.services.async_remove(DOMAIN, SERVICE_REBOOT)
    hass.services.async_remove(DOMAIN, SERVICE_ISAPI_REQUEST)
