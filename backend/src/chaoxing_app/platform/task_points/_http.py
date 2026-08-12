from __future__ import annotations

from collections.abc import Mapping

import requests

from chaoxing_app.platform.errors import (
    PlatformAuthenticationError,
    PlatformConfigurationError,
    PlatformHTTPError,
    PlatformTimeoutError,
    PlatformTransportError,
)

type QueryValue = str | int
type RequestTimeout = tuple[float, float]

_LOGIN_PAGE_MARKER = "\u7528\u6237\u767b\u5f55"
_LOGIN_HOST = "passport2.chaoxing.com"


class TaskPointHTTPClient:
    def __init__(
        self,
        *,
        session: requests.Session,
        timeout: RequestTimeout = (5.0, 15.0),
        tls_verify: bool | str = True,
    ) -> None:
        if len(timeout) != 2 or any(value <= 0 for value in timeout):
            raise PlatformConfigurationError("request timeouts must be positive")
        if tls_verify is False or (isinstance(tls_verify, str) and not tls_verify.strip()):
            raise PlatformConfigurationError("TLS verification cannot be disabled")
        self._session = session
        self._timeout = timeout
        self._tls_verify = tls_verify

    def _get(
        self,
        url: str,
        *,
        operation: str,
        params: Mapping[str, QueryValue],
    ) -> requests.Response:
        try:
            response = self._session.request(
                "GET",
                url,
                params=params,
                timeout=self._timeout,
                verify=self._tls_verify,
            )
        except requests.Timeout as exc:
            raise PlatformTimeoutError(operation) from exc
        except requests.RequestException as exc:
            raise PlatformTransportError(operation) from exc
        if response.status_code != 200:
            raise PlatformHTTPError(operation, response.status_code)
        if _LOGIN_HOST in response.url.casefold() or _LOGIN_PAGE_MARKER in response.text:
            raise PlatformAuthenticationError("platform session is not authenticated")
        return response
