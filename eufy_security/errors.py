"""Define package errors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .api import API


class EufySecurityError(Exception):
    """Define a base error."""


class CannotConnectError(EufySecurityError):
    """Exception for connection failures."""


class ConnectError(EufySecurityError):
    """Connection error (legacy alias)."""


class InvalidCredentialsError(EufySecurityError):
    """Define an error for unauthenticated accounts."""


class RequestError(EufySecurityError):
    """Define an error related to invalid requests."""


class CaptchaRequiredError(EufySecurityError):
    """Exception when CAPTCHA verification is required."""

    def __init__(
        self,
        message: str,
        captcha_id: str,
        captcha_image: str | None = None,
        api: API | None = None,
    ) -> None:
        """Initialize CAPTCHA error with details."""
        super().__init__(message)
        self.captcha_id = captcha_id
        self.captcha_image = captcha_image
        self.api = api  # Store the API instance to reuse for CAPTCHA retry


class InvalidCaptchaError(EufySecurityError):
    """Exception when CAPTCHA answer is invalid."""


class NeedVerifyCodeError(EufySecurityError):
    """Need verification code error."""


class NetworkError(EufySecurityError):
    """Network error."""


class PhoneNoneSupportError(EufySecurityError):
    """Phone none support error."""


class ServerError(EufySecurityError):
    """Server error."""


class VerifyCodeError(EufySecurityError):
    """Verify code error."""


class VerifyCodeExpiredError(EufySecurityError):
    """Verification code has expired."""


class VerifyCodeMaxError(EufySecurityError):
    """Maximum attempts of verifications error."""


class VerifyCodeNoneMatchError(EufySecurityError):
    """Verify code none match error."""


class VerifyCodePasswordError(EufySecurityError):
    """Verify code password error."""


# Map error codes to exceptions
ERRORS: dict[int, type[EufySecurityError]] = {
    997: ConnectError,
    998: NetworkError,
    999: ServerError,
    26006: InvalidCredentialsError,
    26050: InvalidCredentialsError,  # Wrong password
    26051: VerifyCodeExpiredError,
    26052: NeedVerifyCodeError,
    26053: VerifyCodeMaxError,
    26054: VerifyCodeNoneMatchError,
    26055: VerifyCodePasswordError,
    26058: PhoneNoneSupportError,
    100033: InvalidCaptchaError,  # Wrong CAPTCHA answer
}


def raise_error(data: dict[str, Any]) -> None:
    """Raise the appropriate error based upon a response code."""
    code = data.get("code", 0)
    if code == 0:
        return
    cls = ERRORS.get(code, EufySecurityError)
    raise cls(data.get("msg", f"Unknown error (code {code})"))
