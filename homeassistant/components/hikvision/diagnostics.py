"""Diagnostics support for Hikvision integration."""

from __future__ import annotations

import random
import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import HikvisionConfigEntry

TO_REDACT = {
    "password",
    "username",
    "api_key",
    "token",
}


def _anonymize_serial(value: str) -> str:
    """Anonymize a serial number."""
    return re.sub(r"\d", "0", value)


def _anonymize_ip(value: str) -> str:
    """Anonymize an IP address."""
    parts = value.split(".")
    if len(parts) == 4:
        return f"1.0.0.{random.randint(1, 254)}"
    return value


def _anonymize_mac(value: str) -> str:
    """Anonymize a MAC address."""
    return ":".join(f"{random.randint(0, 255):02x}" for _ in range(6))


def _anonymize_data(data: dict[str, Any], anon_map: dict[str, str]) -> dict[str, Any]:
    """Anonymize sensitive data in a dictionary."""
    result = {}

    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = _anonymize_data(value, anon_map)
        elif isinstance(value, list):
            result[key] = [
                _anonymize_data(item, anon_map) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, str):
            lower_key = key.lower()
            if "serial" in lower_key or "deviceid" in lower_key:
                if value not in anon_map:
                    anon_map[value] = _anonymize_serial(value)
                result[key] = anon_map[value]
            elif "ip" in lower_key or "address" in lower_key:
                if value not in anon_map:
                    anon_map[value] = _anonymize_ip(value)
                result[key] = anon_map[value]
            elif "mac" in lower_key:
                if value not in anon_map:
                    anon_map[value] = _anonymize_mac(value)
                result[key] = anon_map[value]
            else:
                result[key] = value
        else:
            result[key] = value

    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HikvisionConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator
    secondary_coordinator = runtime_data.secondary_coordinator

    anon_map: dict[str, str] = {}

    # Build diagnostic data
    diagnostics_data: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "domain": entry.domain,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "device": {
            "device_id": _anonymize_serial(runtime_data.device_id),
            "device_name": runtime_data.device_name,
            "device_type": runtime_data.device_type,
            "device_model": runtime_data.device_model,
            "firmware_version": runtime_data.firmware_version,
        },
    }

    # Add coordinator data if available
    if coordinator.data:
        data = coordinator.data
        diagnostics_data["coordinator"] = {
            "cameras": [
                {
                    "id": camera.id,
                    "name": camera.name,
                    "streams": [
                        {
                            "id": stream.id,
                            "type_id": stream.type_id,
                            "enabled": stream.enabled,
                        }
                        for stream in camera.streams
                    ],
                }
                for camera in data.cameras
            ],
            "event_states": {
                event_id: {
                    "channel": state.channel,
                    "type": state.type,
                    "enabled": state.enabled,
                }
                for event_id, state in data.event_states.items()
            },
            "output_ports": [
                {"id": port.id, "name": port.name} for port in data.output_ports
            ],
            "output_states": data.output_states,
            "holiday_mode_enabled": data.holiday_mode_enabled,
        }

        if data.capabilities:
            diagnostics_data["capabilities"] = {
                "support_holiday_mode": data.capabilities.support_holiday_mode,
                "support_alarm_server": data.capabilities.support_alarm_server,
                "support_io_outputs": data.capabilities.support_io_outputs,
                "support_storage": data.capabilities.support_storage,
                "num_io_outputs": data.capabilities.num_io_outputs,
            }

    # Add secondary coordinator data if available
    if secondary_coordinator.data:
        data = secondary_coordinator.data
        diagnostics_data["secondary_coordinator"] = {
            "storage_devices": [
                _anonymize_data(
                    {
                        "id": storage.id,
                        "name": storage.name,
                        "status": storage.status,
                        "type": storage.type,
                        "capacity": storage.capacity,
                        "free_space": storage.free_space,
                        "ip_address": storage.ip_address,
                    },
                    anon_map,
                )
                for storage in data.storage_devices
            ],
            "alarm_server": (
                _anonymize_data(
                    {
                        "protocol": data.alarm_server.protocol,
                        "address": data.alarm_server.address,
                        "port": data.alarm_server.port,
                        "path": data.alarm_server.path,
                    },
                    anon_map,
                )
                if data.alarm_server
                else None
            ),
        }

    return diagnostics_data
