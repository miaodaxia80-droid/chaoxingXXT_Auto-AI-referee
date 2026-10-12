from __future__ import annotations


class PlatformError(Exception):
    """Base class for failures at the Chaoxing platform boundary."""


class PlatformConfigurationError(PlatformError):
    """Raised when a platform client is configured insecurely or incorrectly."""


class PlatformTransportError(PlatformError):
    def __init__(self, operation: str, *, reason: str = "request failed") -> None:
        super().__init__(f"platform {reason} during {operation}")
        self.operation = operation


class PlatformTimeoutError(PlatformTransportError):
    def __init__(self, operation: str) -> None:
        super().__init__(operation, reason="request timed out")


class PlatformHTTPError(PlatformError):
    def __init__(self, operation: str, status_code: int) -> None:
        super().__init__(f"platform returned HTTP {status_code} during {operation}")
        self.operation = operation
        self.status_code = status_code


def answer_provider_http_reason(status_code: int, *, session_required: bool = False) -> str:
    """Classify a provider HTTP rejection into a stable, user-explainable reason code."""

    if session_required:
        return "provider_session_required"
    if status_code in {401, 403}:
        return "provider_unauthorized"
    if status_code == 404:
        return "provider_not_found"
    if status_code == 429:
        return "provider_rate_limited"
    if status_code >= 500:
        return "provider_unavailable"
    return "provider_http_error"


class AnswerProviderHTTPError(PlatformHTTPError):
    """Sanitized provider rejection; never retains a URL, body or credential."""

    def __init__(self, status_code: int, *, session_required: bool = False) -> None:
        super().__init__("answer provider", status_code)
        self.reason = answer_provider_http_reason(status_code, session_required=session_required)


class PlatformAuthenticationError(PlatformError):
    """Raised when credentials or an existing platform session are rejected."""


class PlatformCaptchaError(PlatformError):
    """Raised when a platform verification challenge cannot be completed."""


class PlatformParseError(PlatformError):
    def __init__(self, resource: str, detail: str) -> None:
        super().__init__(f"could not parse {resource}: {detail}")
        self.resource = resource
        self.detail = detail
