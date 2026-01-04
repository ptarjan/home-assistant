"""ISAPI client for Hikvision devices."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import logging
from typing import Any

import httpx
import xmltodict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

_LOGGER = logging.getLogger(__name__)

# ISAPI Endpoints
ENDPOINT_DEVICE_INFO = "/ISAPI/System/deviceInfo"
ENDPOINT_CAPABILITIES = "/ISAPI/System/capabilities"
ENDPOINT_STORAGE = "/ISAPI/ContentMgmt/Storage"
ENDPOINT_STREAMING_CHANNELS = "/ISAPI/Streaming/channels"
ENDPOINT_IO_INPUTS = "/ISAPI/System/IO/inputs"
ENDPOINT_IO_OUTPUTS = "/ISAPI/System/IO/outputs"
ENDPOINT_EVENT_NOTIFICATION = "/ISAPI/Event/notification/httpHosts"
ENDPOINT_HOLIDAYS = "/ISAPI/System/Holidays"
ENDPOINT_REBOOT = "/ISAPI/System/reboot"
ENDPOINT_EVENT_TRIGGERS = "/ISAPI/Event/triggers"
ENDPOINT_SMART_CAPABILITIES = "/ISAPI/Smart/capabilities"

# Event detection endpoints
EVENT_ENDPOINTS: dict[str, str] = {
    "motionDetection": "/ISAPI/System/Video/inputs/channels/{channel}/motionDetection",
    "lineDetection": "/ISAPI/Smart/LineDetection/{channel}",
    "fieldDetection": "/ISAPI/Smart/FieldDetection/{channel}",
    "regionEntrance": "/ISAPI/Smart/RegionEntrance/{channel}",
    "regionExiting": "/ISAPI/Smart/RegionExiting/{channel}",
    "tamperDetection": "/ISAPI/System/Video/inputs/channels/{channel}/tamperDetection",
    "sceneChangeDetection": "/ISAPI/Smart/SceneChangeDetection/{channel}",
    "PIR": "/ISAPI/WLAlarm/PIR",
    "faceDetection": "/ISAPI/Smart/FaceDetect/{channel}",
}

# Request timeout
REQUEST_TIMEOUT = 20


class HTTPMethod(StrEnum):
    """HTTP methods."""

    GET = "GET"
    PUT = "PUT"
    POST = "POST"
    DELETE = "DELETE"


class ISAPIError(Exception):
    """Base ISAPI error."""


class ISAPIConnectionError(ISAPIError):
    """Connection error."""


class ISAPIAuthError(ISAPIError):
    """Authentication error."""


class ISAPINotFoundError(ISAPIError):
    """Resource not found error."""


@dataclass
class StorageDevice:
    """Storage device information."""

    id: str
    name: str
    status: str
    type: str
    capacity: int | None = None
    free_space: int | None = None
    ip_address: str | None = None


@dataclass
class AlarmServerInfo:
    """Alarm server configuration."""

    protocol: str = ""
    address: str = ""
    port: int = 0
    path: str = ""


@dataclass
class StreamInfo:
    """Camera stream information."""

    id: str
    channel_id: int
    type_id: int
    name: str
    enabled: bool


@dataclass
class CameraInfo:
    """Camera information."""

    id: int
    name: str
    model: str | None = None
    serial_number: str | None = None
    input_port: int | None = None
    streams: list[StreamInfo] = field(default_factory=list)


@dataclass
class OutputPort:
    """Output port information."""

    id: str
    name: str


@dataclass
class EventState:
    """Event detection state."""

    id: str
    channel: int
    type: str
    enabled: bool


@dataclass
class DeviceCapabilities:
    """Device capabilities."""

    support_holiday_mode: bool = False
    support_alarm_server: bool = False
    support_io_outputs: bool = False
    support_storage: bool = False
    num_io_outputs: int = 0


class ISAPIClient:
    """Client for Hikvision ISAPI."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        username: str,
        password: str,
        ssl: bool = False,
    ) -> None:
        """Initialize the ISAPI client."""
        self.hass = hass
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssl = ssl

        protocol = "https" if ssl else "http"
        self.base_url = f"{protocol}://{host}:{port}"

        self._auth: httpx.DigestAuth | httpx.BasicAuth | None = None
        self._device_info: dict[str, Any] = {}
        self._capabilities: DeviceCapabilities | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get HTTP client with proper authentication."""
        return get_async_client(self.hass, verify_ssl=self.ssl)

    async def _detect_auth_method(self) -> None:
        """Detect the authentication method (Basic or Digest)."""
        if self._auth is not None:
            return

        client = await self._get_client()
        url = f"{self.base_url}{ENDPOINT_DEVICE_INFO}"

        # Try digest auth first (more common for Hikvision)
        try:
            digest_auth = httpx.DigestAuth(self.username, self.password)
            response = await client.get(url, auth=digest_auth, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                self._auth = digest_auth
                return
        except Exception:  # noqa: BLE001
            pass

        # Fall back to basic auth
        self._auth = httpx.BasicAuth(self.username, self.password)

    async def request(
        self,
        method: HTTPMethod,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | bytes:
        """Make an ISAPI request."""
        await self._detect_auth_method()

        client = await self._get_client()
        url = f"{self.base_url}{endpoint}"

        try:
            if method == HTTPMethod.GET:
                response = await client.get(
                    url, auth=self._auth, params=params, timeout=REQUEST_TIMEOUT
                )
            elif method == HTTPMethod.PUT:
                xml_data = xmltodict.unparse(data) if data else None
                response = await client.put(
                    url,
                    auth=self._auth,
                    content=xml_data,
                    headers={"Content-Type": "application/xml"},
                    timeout=REQUEST_TIMEOUT,
                )
            elif method == HTTPMethod.POST:
                xml_data = xmltodict.unparse(data) if data else None
                response = await client.post(
                    url,
                    auth=self._auth,
                    content=xml_data,
                    headers={"Content-Type": "application/xml"},
                    timeout=REQUEST_TIMEOUT,
                )
            else:
                response = await client.request(
                    method, url, auth=self._auth, timeout=REQUEST_TIMEOUT
                )

        except httpx.ConnectError as err:
            raise ISAPIConnectionError(f"Cannot connect to {self.host}") from err
        except httpx.TimeoutException as err:
            raise ISAPIConnectionError(f"Timeout connecting to {self.host}") from err

        if response.status_code == 401:
            raise ISAPIAuthError("Invalid credentials")
        if response.status_code == 403:
            raise ISAPIAuthError("Insufficient permissions")
        if response.status_code == 404:
            raise ISAPINotFoundError(f"Endpoint not found: {endpoint}")
        if response.status_code >= 400:
            raise ISAPIError(f"Request failed with status {response.status_code}")

        content_type = response.headers.get("content-type", "")
        if "image" in content_type or "octet-stream" in content_type:
            return response.content

        try:
            return xmltodict.parse(response.text)
        except Exception:  # noqa: BLE001
            return {"raw": response.text}

    async def get_device_info(self) -> dict[str, Any]:
        """Get device information."""
        if not self._device_info:
            response = await self.request(HTTPMethod.GET, ENDPOINT_DEVICE_INFO)
            self._device_info = response.get("DeviceInfo", {})
        return self._device_info

    async def get_device_serial(self) -> str:
        """Get device serial number."""
        info = await self.get_device_info()
        return info.get("serialNumber", "")

    async def get_device_name(self) -> str:
        """Get device name."""
        info = await self.get_device_info()
        return info.get("deviceName", "")

    async def get_device_model(self) -> str:
        """Get device model."""
        info = await self.get_device_info()
        return info.get("model", "")

    async def get_device_type(self) -> str:
        """Get device type (e.g., NVR, DVR, IPCamera)."""
        info = await self.get_device_info()
        return info.get("deviceType", "Camera")

    async def get_capabilities(self) -> DeviceCapabilities:
        """Get device capabilities."""
        if self._capabilities is not None:
            return self._capabilities

        capabilities = DeviceCapabilities()

        # Check for holiday mode support
        try:
            await self.request(HTTPMethod.GET, ENDPOINT_HOLIDAYS)
            capabilities.support_holiday_mode = True
        except ISAPINotFoundError:
            pass
        except ISAPIError:
            pass

        # Check for alarm server support
        try:
            await self.request(HTTPMethod.GET, ENDPOINT_EVENT_NOTIFICATION)
            capabilities.support_alarm_server = True
        except ISAPINotFoundError:
            pass
        except ISAPIError:
            pass

        # Check for IO outputs
        try:
            response = await self.request(HTTPMethod.GET, ENDPOINT_IO_OUTPUTS)
            outputs = response.get("IOOutputPortList", {}).get("IOOutputPort", [])
            if isinstance(outputs, dict):
                outputs = [outputs]
            capabilities.support_io_outputs = len(outputs) > 0
            capabilities.num_io_outputs = len(outputs)
        except ISAPINotFoundError:
            pass
        except ISAPIError:
            pass

        # Check for storage
        try:
            await self.request(HTTPMethod.GET, ENDPOINT_STORAGE)
            capabilities.support_storage = True
        except ISAPINotFoundError:
            pass
        except ISAPIError:
            pass

        self._capabilities = capabilities
        return capabilities

    async def get_storage_devices(self) -> list[StorageDevice]:
        """Get storage device information."""
        try:
            response = await self.request(HTTPMethod.GET, ENDPOINT_STORAGE)
        except ISAPINotFoundError:
            return []

        devices = []
        storage_list = response.get("storage", {})

        # Handle HDD list
        hdd_list = storage_list.get("hddList", {}).get("hdd", [])
        if isinstance(hdd_list, dict):
            hdd_list = [hdd_list]

        devices.extend(
            StorageDevice(
                id=hdd.get("id", ""),
                name=hdd.get("hddName", f"HDD {hdd.get('id', '')}"),
                status=hdd.get("status", "unknown"),
                type="HDD",
                capacity=self._parse_capacity(hdd.get("capacity")),
                free_space=self._parse_capacity(hdd.get("freeSpace")),
            )
            for hdd in hdd_list
        )

        # Handle NAS list
        nas_list = storage_list.get("nasList", {}).get("nas", [])
        if isinstance(nas_list, dict):
            nas_list = [nas_list]

        devices.extend(
            StorageDevice(
                id=nas.get("id", ""),
                name=nas.get("nasName", f"NAS {nas.get('id', '')}"),
                status=nas.get("status", "unknown"),
                type="NAS",
                capacity=self._parse_capacity(nas.get("capacity")),
                free_space=self._parse_capacity(nas.get("freeSpace")),
                ip_address=nas.get("ipAddress"),
            )
            for nas in nas_list
        )

        return devices

    def _parse_capacity(self, value: str | None) -> int | None:
        """Parse capacity value to bytes."""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    async def get_alarm_server_info(self) -> AlarmServerInfo:
        """Get alarm server configuration."""
        try:
            response = await self.request(HTTPMethod.GET, ENDPOINT_EVENT_NOTIFICATION)
        except ISAPINotFoundError:
            return AlarmServerInfo()

        hosts = response.get("HttpHostNotificationList", {}).get(
            "HttpHostNotification", []
        )
        if isinstance(hosts, dict):
            hosts = [hosts]

        if not hosts:
            return AlarmServerInfo()

        # Get first configured host
        host = hosts[0]
        return AlarmServerInfo(
            protocol=host.get("protocolType", ""),
            address=host.get("ipAddress", host.get("hostName", "")),
            port=int(host.get("portNo", 0)),
            path=host.get("url", ""),
        )

    async def get_streaming_channels(self) -> list[StreamInfo]:
        """Get streaming channel information."""
        try:
            response = await self.request(HTTPMethod.GET, ENDPOINT_STREAMING_CHANNELS)
        except ISAPINotFoundError:
            return []

        channels = response.get("StreamingChannelList", {}).get("StreamingChannel", [])
        if isinstance(channels, dict):
            channels = [channels]

        streams = []
        for channel in channels:
            channel_id = channel.get("id", "")
            # Format: 101, 102, 201, 202 where first digit is camera, second is stream
            try:
                full_id = int(channel_id)
                cam_id = full_id // 100
                stream_type = full_id % 100
            except (ValueError, TypeError):
                continue

            streams.append(
                StreamInfo(
                    id=channel_id,
                    channel_id=cam_id,
                    type_id=stream_type,
                    name=channel.get("channelName", f"Channel {cam_id}"),
                    enabled=channel.get("enabled", "true").lower() == "true",
                )
            )

        return streams

    async def get_cameras(self) -> list[CameraInfo]:
        """Get camera information with streams."""
        streams = await self.get_streaming_channels()

        # Group streams by camera ID
        cameras_dict: dict[int, CameraInfo] = {}
        for stream in streams:
            if stream.channel_id not in cameras_dict:
                cameras_dict[stream.channel_id] = CameraInfo(
                    id=stream.channel_id,
                    name=stream.name,
                    streams=[],
                )
            cameras_dict[stream.channel_id].streams.append(stream)

        return list(cameras_dict.values())

    async def get_output_ports(self) -> list[OutputPort]:
        """Get output port information."""
        try:
            response = await self.request(HTTPMethod.GET, ENDPOINT_IO_OUTPUTS)
        except ISAPINotFoundError:
            return []

        outputs = response.get("IOOutputPortList", {}).get("IOOutputPort", [])
        if isinstance(outputs, dict):
            outputs = [outputs]

        return [
            OutputPort(
                id=output.get("id", ""),
                name=output.get("outputName", f"Output {output.get('id', '')}"),
            )
            for output in outputs
        ]

    async def get_output_state(self, output_id: str) -> bool:
        """Get output port state."""
        try:
            response = await self.request(
                HTTPMethod.GET, f"{ENDPOINT_IO_OUTPUTS}/{output_id}/status"
            )
            status = response.get("IOPortStatus", {})
            return status.get("ioState", "inactive").lower() == "active"
        except ISAPIError:
            return False

    async def set_output_state(self, output_id: str, state: bool) -> None:
        """Set output port state."""
        data = {
            "IOPortData": {
                "@version": "2.0",
                "@xmlns": "http://www.isapi.org/ver20/XMLSchema",
                "outputState": "high" if state else "low",
            }
        }
        await self.request(
            HTTPMethod.PUT, f"{ENDPOINT_IO_OUTPUTS}/{output_id}/trigger", data=data
        )

    async def get_holiday_mode_enabled(self) -> bool:
        """Get holiday mode status."""
        try:
            response = await self.request(HTTPMethod.GET, ENDPOINT_HOLIDAYS)
            holidays = response.get("HolidayList", {}).get("holiday", [])
            if isinstance(holidays, dict):
                holidays = [holidays]
            return any(h.get("enabled", "false").lower() == "true" for h in holidays)
        except ISAPIError:
            return False

    async def set_holiday_mode_enabled(self, enabled: bool) -> None:
        """Set holiday mode status."""
        # Get current holiday config
        try:
            response = await self.request(HTTPMethod.GET, ENDPOINT_HOLIDAYS)
        except ISAPIError:
            return

        holidays = response.get("HolidayList", {}).get("holiday", [])
        if isinstance(holidays, dict):
            holidays = [holidays]

        if not holidays:
            return

        # Update first holiday entry
        holidays[0]["enabled"] = "true" if enabled else "false"

        data = {"HolidayList": {"holiday": holidays}}
        await self.request(HTTPMethod.PUT, ENDPOINT_HOLIDAYS, data=data)

    async def get_event_states(self) -> list[EventState]:
        """Get event detection states for all channels."""
        states = []
        cameras = await self.get_cameras()

        for camera in cameras:
            channel = camera.id
            for event_type, endpoint_template in EVENT_ENDPOINTS.items():
                endpoint = endpoint_template.format(channel=channel)
                try:
                    response = await self.request(HTTPMethod.GET, endpoint)
                    # Find the enabled field in the response
                    for value in response.values():
                        if isinstance(value, dict) and "enabled" in value:
                            enabled = value.get("enabled", "false").lower() == "true"
                            states.append(
                                EventState(
                                    id=f"{event_type}_{channel}",
                                    channel=channel,
                                    type=event_type,
                                    enabled=enabled,
                                )
                            )
                            break
                except ISAPIError:
                    continue

        return states

    async def set_event_enabled(
        self, event_type: str, channel: int, enabled: bool
    ) -> None:
        """Set event detection enabled state."""
        endpoint_template = EVENT_ENDPOINTS.get(event_type)
        if not endpoint_template:
            raise ISAPIError(f"Unknown event type: {event_type}")

        endpoint = endpoint_template.format(channel=channel)

        # Get current config
        try:
            response = await self.request(HTTPMethod.GET, endpoint)
        except ISAPIError as err:
            raise ISAPIError(f"Cannot get event config: {err}") from err

        # Update enabled state
        for value in response.values():
            if isinstance(value, dict) and "enabled" in value:
                value["enabled"] = "true" if enabled else "false"
                break

        await self.request(HTTPMethod.PUT, endpoint, data=response)

    async def get_snapshot(
        self, channel: int, width: int | None = None, height: int | None = None
    ) -> bytes:
        """Get camera snapshot."""
        stream_id = channel * 100 + 1  # Main stream
        endpoint = f"{ENDPOINT_STREAMING_CHANNELS}/{stream_id}/picture"

        params = {}
        if width:
            params["width"] = width
        if height:
            params["height"] = height

        result = await self.request(HTTPMethod.GET, endpoint, params=params or None)
        if isinstance(result, bytes):
            return result
        raise ISAPIError("Failed to get snapshot")

    def get_rtsp_url(self, channel: int, stream_type: int = 1) -> str:
        """Get RTSP URL for a channel."""
        stream_id = channel * 100 + stream_type
        protocol = "rtsps" if self.ssl else "rtsp"
        rtsp_port = 554  # Default RTSP port
        return (
            f"{protocol}://{self.username}:{self.password}@"
            f"{self.host}:{rtsp_port}/Streaming/Channels/{stream_id}"
        )

    async def reboot(self) -> None:
        """Reboot the device."""
        await self.request(HTTPMethod.PUT, ENDPOINT_REBOOT)

    async def custom_request(
        self,
        method: str,
        endpoint: str,
        data: str | None = None,
    ) -> dict[str, Any]:
        """Make a custom ISAPI request."""
        http_method = HTTPMethod(method.upper())

        if data:
            try:
                parsed_data = xmltodict.parse(data)
            except Exception as err:
                raise ISAPIError(f"Invalid XML data: {err}") from err
        else:
            parsed_data = None

        result = await self.request(http_method, endpoint, data=parsed_data)
        if isinstance(result, bytes):
            return {"raw_bytes": True, "length": len(result)}
        return result

    async def close(self) -> None:
        """Close the client (cleanup if needed)."""
        # The httpx client is managed by Home Assistant
