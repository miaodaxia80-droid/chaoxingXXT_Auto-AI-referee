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


class PlatformAuthenticationError(PlatformError):
    """Raised when credentials or an existing platform session are rejected."""


class PlatformCaptchaError(PlatformError):
    """Raised when a platform verification challenge cannot be completed."""


class PlatformParseError(PlatformError):
    def __init__(self, resource: str, detail: str) -> None:
        super().__init__(f"could not parse {resource}: {detail}")
        self.resource = resource
        self.detail = detail
