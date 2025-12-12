"""Python library for Eufy Security cameras and devices.

Based on eufy-security-client by bropat.
"""

from .api import API, async_login

# Alias for Home Assistant integration compatibility
EufySecurityAPI = API
from .device import Camera, Station
from .errors import (
    CannotConnectError,
    CaptchaRequiredError,
    EufySecurityError,
    InvalidCaptchaError,
    InvalidCredentialsError,
    RequestError,
)

__all__ = [
    "API",
    "EufySecurityAPI",
    "async_login",
    "Camera",
    "Station",
    "EufySecurityError",
    "InvalidCredentialsError",
    "RequestError",
    "CannotConnectError",
    "CaptchaRequiredError",
    "InvalidCaptchaError",
]

__version__ = "1.0.0"
