from __future__ import annotations

import math
from collections.abc import Mapping
from ipaddress import ip_address
from typing import Final, cast
from urllib.parse import urlsplit

import requests

from chaoxing_app.platform.errors import PlatformConfigurationError
from chaoxing_app.platform.task_points.quiz import ProviderAnswer

type RequestTimeout = tuple[float, float]
type TLSVerify = bool | str

DEFAULT_TIMEOUT: Final[RequestTimeout] = (5.0, 30.0)


def require_nonempty(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise PlatformConfigurationError(f"answer provider {field} is required")
    return normalized


def validate_endpoint(value: str, *, allow_unsafe_endpoint: bool = False) -> str:
    if not isinstance(allow_unsafe_endpoint, bool):
        raise PlatformConfigurationError("answer provider endpoint policy is invalid")
    endpoint = value.strip().rstrip("/")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise PlatformConfigurationError("answer provider endpoint is invalid") from None
    del port
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PlatformConfigurationError("answer provider endpoint is invalid")
    if not allow_unsafe_endpoint and (
        parsed.scheme.casefold() != "https" or _is_local_endpoint(hostname)
    ):
        raise PlatformConfigurationError("answer provider endpoint is not allowed")
    return endpoint


def _is_local_endpoint(hostname: str) -> bool:
    normalized = hostname.rstrip(".").casefold()
    if (
        normalized == "localhost"
        or normalized.endswith(".localhost")
        or normalized == "local"
        or normalized.endswith(".local")
    ):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def as_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast(Mapping[str, object], value)


def answer_from_values(
    values: list[str] | tuple[str, ...],
    *,
    source: str,
) -> ProviderAnswer | None:
    normalized = tuple(value.strip() for value in values)
    if not normalized or any(not value for value in normalized):
        return None
    value: str | tuple[str, ...] = normalized[0] if len(normalized) == 1 else normalized
    return ProviderAnswer(value=value, source=source)


class SafeAnswerProvider:
    configured: Final = True

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class JSONHTTPClient:
    def __init__(
        self,
        *,
        endpoint: str,
        session: requests.Session | None,
        timeout: RequestTimeout,
        tls_verify: TLSVerify,
        allow_unsafe_endpoint: bool = False,
    ) -> None:
        if (
            not isinstance(timeout, tuple)
            or len(timeout) != 2
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
                for value in timeout
            )
        ):
            raise PlatformConfigurationError("answer provider timeouts must be positive")
        if (
            not isinstance(tls_verify, (bool, str))
            or tls_verify is False
            or (isinstance(tls_verify, str) and not tls_verify.strip())
        ):
            raise PlatformConfigurationError("TLS verification cannot be disabled")
        self._endpoint = validate_endpoint(
            endpoint,
            allow_unsafe_endpoint=allow_unsafe_endpoint,
        )
        self._session = session or requests.Session()
        self._timeout = timeout
        self._tls_verify = tls_verify

    def request(
        self,
        method: str,
        *,
        params: Mapping[str, str | int | float] | None = None,
        json_body: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> object | None:
        try:
            response = self._session.request(
                method,
                self._endpoint,
                params=params,
                json=json_body,
                headers=headers,
                timeout=self._timeout,
                verify=self._tls_verify,
                allow_redirects=False,
            )
            if response.status_code != 200:
                return None
            payload: object = response.json()
            return payload
        except Exception:
            # Provider failures are intentionally opaque: upstream treats None as unanswered.
            return None
