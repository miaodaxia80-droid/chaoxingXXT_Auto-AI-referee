import pytest
from pydantic import ValidationError

from chaoxing_app.settings import AppSettings, normalize_http_origin


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.COM:443/", "https://example.com"),
        ("http://example.com:8080", "http://example.com:8080"),
        ("https://[2001:db8::1]:8443", "https://[2001:db8::1]:8443"),
    ],
)
def test_normalize_http_origin(raw: str, expected: str) -> None:
    assert normalize_http_origin(raw) == expected


@pytest.mark.parametrize(
    "origin",
    ["null", "ftp://example.com", "https://user@example.com", "https://example.com/path"],
)
def test_invalid_allowed_origin_fails_at_startup(origin: str) -> None:
    with pytest.raises(ValidationError, match="invalid HTTP origin"):
        AppSettings(allowed_origins=(origin,))


def test_allowed_origins_are_normalized_and_deduplicated() -> None:
    settings = AppSettings(
        allowed_origins=("HTTPS://Example.COM:443/", "https://example.com")
    )
    assert settings.allowed_origins == ("https://example.com",)
