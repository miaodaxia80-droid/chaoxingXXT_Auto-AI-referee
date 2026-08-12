from __future__ import annotations

import json

import pytest
import requests

from chaoxing_app.platform.cookies import dump_cookie_data, load_cookie_data
from chaoxing_app.platform.errors import PlatformConfigurationError


def test_empty_cookie_data_is_a_noop_and_empty_jar_dumps_to_none() -> None:
    jar = requests.cookies.RequestsCookieJar()
    jar.set("existing", "value")

    load_cookie_data(jar, "  \t\r\n")

    assert jar.get_dict() == {"existing": "value"}
    assert dump_cookie_data(requests.cookies.RequestsCookieJar()) is None


def test_legacy_cookie_header_is_loaded() -> None:
    jar = requests.cookies.RequestsCookieJar()

    load_cookie_data(jar, "_uid=12345; token=session-value")

    assert jar.get_dict() == {"_uid": "12345", "token": "session-value"}


def test_structured_cookie_data_round_trips_metadata_deterministically() -> None:
    source = requests.cookies.RequestsCookieJar()
    source.set(
        "token",
        "session-value",
        domain=".chaoxing.com",
        path="/course",
        secure=True,
        expires=2_000_000_000,
    )
    source.set("_uid", "12345", domain="", path="/")

    serialized = dump_cookie_data(source)

    assert serialized is not None
    payload = json.loads(serialized)
    assert payload["version"] == 1
    assert [item["name"] for item in payload["cookies"]] == ["_uid", "token"]

    restored = requests.cookies.RequestsCookieJar()
    load_cookie_data(restored, serialized)

    assert dump_cookie_data(restored) == serialized
    cookies = {cookie.name: cookie for cookie in restored}
    assert cookies["_uid"].value == "12345"
    assert cookies["token"].value == "session-value"
    assert cookies["token"].domain == ".chaoxing.com"
    assert cookies["token"].path == "/course"
    assert cookies["token"].secure is True
    assert cookies["token"].expires == 2_000_000_000


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 2, "cookies": []},
        {"version": 1},
        {"version": 1, "cookies": "not-a-list"},
        {"version": 1, "cookies": [None]},
        {"version": 1, "cookies": [{"name": "", "value": "value"}]},
        {"version": 1, "cookies": [{"name": "token", "value": 123}]},
        {
            "version": 1,
            "cookies": [{"name": "token", "value": "value", "domain": 123}],
        },
        {
            "version": 1,
            "cookies": [{"name": "token", "value": "value", "path": 123}],
        },
        {
            "version": 1,
            "cookies": [{"name": "token", "value": "value", "secure": "yes"}],
        },
        {
            "version": 1,
            "cookies": [{"name": "token", "value": "value", "expires": 1.5}],
        },
    ],
)
def test_invalid_structured_cookie_data_is_rejected(payload: object) -> None:
    jar = requests.cookies.RequestsCookieJar()

    with pytest.raises(PlatformConfigurationError, match="stored cookie data"):
        load_cookie_data(jar, json.dumps(payload))

    assert len(jar) == 0


def test_invalid_json_and_cookie_headers_are_rejected() -> None:
    jar = requests.cookies.RequestsCookieJar()

    with pytest.raises(PlatformConfigurationError, match="invalid JSON"):
        load_cookie_data(jar, "{")
    with pytest.raises(PlatformConfigurationError, match="cookie header is invalid"):
        load_cookie_data(jar, "not a cookie header")


def test_cookie_count_limit_is_enforced_for_both_storage_formats() -> None:
    structured = {
        "version": 1,
        "cookies": [
            {"name": f"cookie-{index}", "value": "value"} for index in range(101)
        ],
    }
    header = "; ".join(f"cookie-{index}=value" for index in range(101))

    with pytest.raises(PlatformConfigurationError, match="stored cookie data is invalid"):
        load_cookie_data(requests.cookies.RequestsCookieJar(), json.dumps(structured))
    with pytest.raises(PlatformConfigurationError, match="cookie header is invalid"):
        load_cookie_data(requests.cookies.RequestsCookieJar(), header)
