# python-eufy-security

[![CI](https://github.com/ptarjan/pyeufysecurity/workflows/CI/badge.svg)](https://github.com/ptarjan/pyeufysecurity/actions)
[![PyPi](https://img.shields.io/pypi/v/python-eufy-security.svg)](https://pypi.python.org/pypi/python-eufy-security)
[![Version](https://img.shields.io/pypi/pyversions/python-eufy-security.svg)](https://pypi.python.org/pypi/python-eufy-security)
[![License](https://img.shields.io/pypi/l/python-eufy-security.svg)](https://github.com/ptarjan/pyeufysecurity/blob/dev/LICENSE)

A Python library for Eufy Security cameras and devices.

Based on [eufy-security-client](https://github.com/bropat/eufy-security-client) by bropat.

## Python Versions

The library is currently supported on:

* Python 3.11
* Python 3.12
* Python 3.13

## Installation

```bash
pip install python-eufy-security
```

## Account Information

Because of the way the Eufy Security private API works, an email/password combo cannot
work with _both_ the Eufy Security mobile app _and_ this library. It is recommended to
use the mobile app to create a secondary "guest" account with a separate email address
and use it with this library.

## Features

- Async authentication with Eufy Security cloud API using v2 encrypted protocol
- ECDH key exchange for secure communication
- CAPTCHA support for login verification
- Automatic token refresh when expired
- Automatic domain switching for regional API endpoints
- Retry on 401 authentication errors
- Camera listing and management
- RTSP stream start/stop (local and cloud)
- Station/hub listing
- Event history for camera thumbnails

## Usage

Everything starts with an [aiohttp](https://aiohttp.readthedocs.io/en/stable/) `ClientSession`:

```python
import asyncio

from aiohttp import ClientSession

from eufy_security import async_login


async def main() -> None:
    """Create the aiohttp session and run the example."""
    async with ClientSession() as session:
        # Create an API client:
        api = await async_login(EUFY_EMAIL, EUFY_PASSWORD, session)

        # Loop through the cameras associated with the account:
        for camera in api.cameras.values():
            print(f"Camera Name: {camera.name}")
            print(f"Serial Number: {camera.serial}")
            print(f"Station Serial Number: {camera.station_serial}")
            print(f"Last Camera Image URL: {camera.last_camera_image_url}")

            print("Starting RTSP Stream")
            stream_url = await camera.async_start_stream()
            print(f"Stream URL: {stream_url}")

            print("Stopping RTSP Stream")
            await camera.async_stop_stream()


asyncio.run(main())
```

## CAPTCHA Handling

Eufy may require CAPTCHA verification for new logins. Handle `CaptchaRequiredError`:

```python
from eufy_security import async_login, CaptchaRequiredError

try:
    api = await async_login(email, password, session)
except CaptchaRequiredError as err:
    # err.captcha_image contains a base64-encoded image
    # Prompt user to solve CAPTCHA, then retry:
    api = await async_login(
        email, password, session,
        captcha_id=err.captcha_id,
        captcha_code=user_provided_code,
        api=err.api,  # Reuse API instance for same ECDH keys
    )
```

## Contributing

1. [Check for open features/bugs](https://github.com/ptarjan/pyeufysecurity/issues)
   or [initiate a discussion on one](https://github.com/ptarjan/pyeufysecurity/issues/new).
2. [Fork the repository](https://github.com/ptarjan/pyeufysecurity/fork).
3. Install the dev environment: `make init`.
4. Enter the virtual environment: `source ./venv/bin/activate`
5. Code your new feature or bug fix.
6. Write a test that covers your new functionality.
7. Update `README.md` with any new documentation.
8. Run tests and ensure 100% code coverage: `make coverage`
9. Ensure you have no linting errors: `make lint`
10. Ensure you have typed your code correctly: `make typing`
11. Submit a pull request!

## License

MIT License
