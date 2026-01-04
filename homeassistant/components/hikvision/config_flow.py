"""Config flow for Hikvision integration."""

from __future__ import annotations

import logging
from typing import Any

from pyhik.hikvision import HikCamera
import requests
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers.typing import ConfigType

from .const import CONF_RTSP_PORT, DEFAULT_PORT, DEFAULT_RTSP_PORT, DOMAIN
from .isapi import ISAPIAuthError, ISAPIClient, ISAPIConnectionError, ISAPIError

_LOGGER = logging.getLogger(__name__)


class HikvisionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hikvision."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: ConfigEntry | None = None
        self._reconfigure_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            ssl = user_input[CONF_SSL]

            protocol = "https" if ssl else "http"
            url = f"{protocol}://{host}"

            try:
                camera = await self.hass.async_add_executor_job(
                    HikCamera, url, port, username, password, ssl
                )
            except requests.exceptions.RequestException:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                device_id = camera.get_id
                device_name = camera.get_name
                if device_id is None:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(device_id)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=device_name or host,
                        data={
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            CONF_SSL: ssl,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_SSL, default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_import(self, import_data: ConfigType) -> ConfigFlowResult:
        """Handle import from configuration.yaml."""
        host = import_data[CONF_HOST]
        port = import_data.get(CONF_PORT, DEFAULT_PORT)
        username = import_data[CONF_USERNAME]
        password = import_data[CONF_PASSWORD]
        ssl = import_data.get(CONF_SSL, False)
        name = import_data.get(CONF_NAME)

        protocol = "https" if ssl else "http"
        url = f"{protocol}://{host}"

        try:
            camera = await self.hass.async_add_executor_job(
                HikCamera, url, port, username, password, ssl
            )
        except requests.exceptions.RequestException:
            return self.async_abort(reason="cannot_connect")
        except Exception:
            _LOGGER.exception("Unexpected exception")
            return self.async_abort(reason="unknown")

        device_id = camera.get_id
        device_name = camera.get_name
        if device_id is None:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=name or device_name or host,
            data={
                CONF_HOST: host,
                CONF_PORT: port,
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
                CONF_SSL: ssl,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauthentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauthentication confirmation."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        assert entry is not None

        if user_input is not None:
            host = entry.data[CONF_HOST]
            port = entry.data[CONF_PORT]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            ssl = entry.data[CONF_SSL]

            # Validate credentials using ISAPI client
            isapi_client = ISAPIClient(self.hass, host, port, username, password, ssl)

            try:
                await isapi_client.get_device_info()
            except ISAPIAuthError:
                errors["base"] = "invalid_auth"
            except ISAPIConnectionError:
                errors["base"] = "cannot_connect"
            except ISAPIError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration."""
        self._reconfigure_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reconfigure_confirm()

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration confirmation."""
        errors: dict[str, str] = {}
        entry = self._reconfigure_entry
        assert entry is not None

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            ssl = user_input[CONF_SSL]

            protocol = "https" if ssl else "http"
            url = f"{protocol}://{host}"

            try:
                camera = await self.hass.async_add_executor_job(
                    HikCamera, url, port, username, password, ssl
                )
            except requests.exceptions.RequestException:
                _LOGGER.exception("Error connecting to Hikvision device")
                errors["base"] = "cannot_connect"
            else:
                device_id = camera.get_id
                if device_id is None:
                    errors["base"] = "cannot_connect"
                elif device_id != entry.unique_id:
                    # Prevent changing to a different device
                    return self.async_abort(reason="wrong_device")
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            CONF_SSL: ssl,
                        },
                    )

        return self.async_show_form(
            step_id="reconfigure_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                    vol.Required(CONF_PORT, default=entry.data[CONF_PORT]): int,
                    vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_SSL, default=entry.data[CONF_SSL]): bool,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow."""
        return HikvisionOptionsFlow()


class HikvisionOptionsFlow(OptionsFlow):
    """Handle Hikvision options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_RTSP_PORT,
                        default=self.config_entry.options.get(
                            CONF_RTSP_PORT, DEFAULT_RTSP_PORT
                        ),
                    ): int,
                }
            ),
        )
