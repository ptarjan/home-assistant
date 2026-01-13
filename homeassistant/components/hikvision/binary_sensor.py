"""Support for Hikvision event stream events represented as binary sensors."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.binary_sensor import (
    PLATFORM_SCHEMA as BINARY_SENSOR_PLATFORM_SCHEMA,
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    ATTR_LAST_TRIP_TIME,
    CONF_CUSTOMIZE,
    CONF_DELAY,
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    EntityCategory,
)
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    AddEntitiesCallback,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import HikvisionConfigEntry
from .const import DEFAULT_PORT, DOMAIN

CONF_IGNORED = "ignored"

DEFAULT_DELAY = 0
DEFAULT_IGNORED = False


@dataclass(frozen=True, kw_only=True)
class HikvisionBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Hikvision binary sensor entity."""


# Entity descriptions for known Hikvision event types
# The key matches the sensor_type from pyhik (the friendly name from SENSOR_MAP)
BINARY_SENSOR_DESCRIPTIONS: dict[str, HikvisionBinarySensorEntityDescription] = {
    "Motion": HikvisionBinarySensorEntityDescription(
        key="motion",
        translation_key="motion",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    "Line Crossing": HikvisionBinarySensorEntityDescription(
        key="line_crossing",
        translation_key="line_crossing",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    "Field Detection": HikvisionBinarySensorEntityDescription(
        key="field_detection",
        translation_key="field_detection",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    "Tamper Detection": HikvisionBinarySensorEntityDescription(
        key="tamper_detection",
        translation_key="tamper_detection",
        device_class=BinarySensorDeviceClass.TAMPER,
    ),
    "Shelter Alarm": HikvisionBinarySensorEntityDescription(
        key="shelter_alarm",
        translation_key="shelter_alarm",
    ),
    "Disk Full": HikvisionBinarySensorEntityDescription(
        key="disk_full",
        translation_key="disk_full",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "Disk Error": HikvisionBinarySensorEntityDescription(
        key="disk_error",
        translation_key="disk_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "Net Interface Broken": HikvisionBinarySensorEntityDescription(
        key="net_interface_broken",
        translation_key="net_interface_broken",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "IP Conflict": HikvisionBinarySensorEntityDescription(
        key="ip_conflict",
        translation_key="ip_conflict",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "Illegal Access": HikvisionBinarySensorEntityDescription(
        key="illegal_access",
        translation_key="illegal_access",
        device_class=BinarySensorDeviceClass.SAFETY,
    ),
    "Video Mismatch": HikvisionBinarySensorEntityDescription(
        key="video_mismatch",
        translation_key="video_mismatch",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "Bad Video": HikvisionBinarySensorEntityDescription(
        key="bad_video",
        translation_key="bad_video",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PIR Alarm": HikvisionBinarySensorEntityDescription(
        key="pir_alarm",
        translation_key="pir_alarm",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    "Face Detection": HikvisionBinarySensorEntityDescription(
        key="face_detection",
        translation_key="face_detection",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    "Scene Change Detection": HikvisionBinarySensorEntityDescription(
        key="scene_change_detection",
        translation_key="scene_change_detection",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    "I/O": HikvisionBinarySensorEntityDescription(
        key="io",
        translation_key="io",
    ),
    "Unattended Baggage": HikvisionBinarySensorEntityDescription(
        key="unattended_baggage",
        translation_key="unattended_baggage",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    "Attended Baggage": HikvisionBinarySensorEntityDescription(
        key="attended_baggage",
        translation_key="attended_baggage",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    "Recording Failure": HikvisionBinarySensorEntityDescription(
        key="recording_failure",
        translation_key="recording_failure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "Exiting Region": HikvisionBinarySensorEntityDescription(
        key="exiting_region",
        translation_key="exiting_region",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    "Entering Region": HikvisionBinarySensorEntityDescription(
        key="entering_region",
        translation_key="entering_region",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
}

_LOGGER = logging.getLogger(__name__)

CUSTOMIZE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_IGNORED, default=DEFAULT_IGNORED): cv.boolean,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_int,
    }
)

PLATFORM_SCHEMA = BINARY_SENSOR_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_NAME): cv.string,
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_SSL, default=False): cv.boolean,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_CUSTOMIZE, default={}): vol.Schema(
            {cv.string: CUSTOMIZE_SCHEMA}
        ),
    }
)

PARALLEL_UPDATES = 0


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Hikvision binary sensor platform from YAML."""
    # Trigger the import flow to migrate YAML config to config entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=config
    )

    if (
        result.get("type") is FlowResultType.ABORT
        and result.get("reason") != "already_configured"
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"deprecated_yaml_import_issue_{result.get('reason')}",
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="deprecated_yaml_import_issue",
            translation_placeholders={
                "domain": DOMAIN,
                "integration_title": "Hikvision",
            },
        )
        return

    ir.async_create_issue(
        hass,
        HOMEASSISTANT_DOMAIN,
        f"deprecated_yaml_{DOMAIN}",
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
        translation_placeholders={
            "domain": DOMAIN,
            "integration_title": "Hikvision",
        },
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Hikvision binary sensors from a config entry."""
    data = entry.runtime_data
    camera = data.camera

    sensors = camera.current_event_states
    if sensors is None or not sensors:
        _LOGGER.warning("Hikvision device has no sensors available")
        return

    async_add_entities(
        HikvisionBinarySensor(
            entry=entry,
            description=BINARY_SENSOR_DESCRIPTIONS.get(
                sensor_type,
                HikvisionBinarySensorEntityDescription(
                    key=sensor_type.lower().replace(" ", "_"),
                ),
            ),
            sensor_type=sensor_type,
            channel=channel_info[1],
        )
        for sensor_type, channel_list in sensors.items()
        for channel_info in channel_list
    )


class HikvisionBinarySensor(BinarySensorEntity):
    """Representation of a Hikvision binary sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: HikvisionBinarySensorEntityDescription

    def __init__(
        self,
        entry: HikvisionConfigEntry,
        description: HikvisionBinarySensorEntityDescription,
        sensor_type: str,
        channel: int,
    ) -> None:
        """Initialize the binary sensor."""
        self.entity_description = description
        self._data = entry.runtime_data
        self._camera = self._data.camera
        self._sensor_type = sensor_type
        self._channel = channel

        # Build unique ID
        self._attr_unique_id = f"{self._data.device_id}_{sensor_type}_{channel}"

        # Device info for device registry
        if self._data.device_type == "NVR":
            # NVR channels get their own device linked to the NVR via via_device
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{self._data.device_id}_{channel}")},
                via_device=(DOMAIN, self._data.device_id),
                name=f"{self._data.device_name} Channel {channel}",
                manufacturer="Hikvision",
                model="NVR Channel",
            )
        else:
            # Single camera device
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, self._data.device_id)},
                name=self._data.device_name,
                manufacturer="Hikvision",
                model=self._data.device_type,
            )

        # For unknown sensor types without translation_key, use sensor_type as name
        if not description.translation_key:
            self._attr_translation_key = None
            self._attr_name = sensor_type

        # Callback ID for pyhik
        self._callback_id = f"{self._data.device_id}.{sensor_type}.{channel}"

    def _get_sensor_attributes(self) -> tuple[bool, Any, Any, Any]:
        """Get sensor attributes from camera."""
        return self._camera.fetch_attributes(self._sensor_type, self._channel)

    @property
    def is_on(self) -> bool:
        """Return true if sensor is on."""
        return self._get_sensor_attributes()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attrs = self._get_sensor_attributes()
        return {ATTR_LAST_TRIP_TIME: attrs[3]}

    async def async_added_to_hass(self) -> None:
        """Register callback when entity is added."""
        await super().async_added_to_hass()

        # Register callback with pyhik
        self._camera.add_update_callback(self._update_callback, self._callback_id)

    def _update_callback(self, msg: str) -> None:
        """Update the sensor's state when callback is triggered.

        This is called from pyhik's event stream thread, so we use
        schedule_update_ha_state which is thread-safe.
        """
        self.schedule_update_ha_state()
