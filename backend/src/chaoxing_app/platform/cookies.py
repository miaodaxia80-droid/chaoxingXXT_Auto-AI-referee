from __future__ import annotations

import json
from http.cookies import SimpleCookie
from typing import Any

import requests

from chaoxing_app.platform.errors import PlatformConfigurationError

_COOKIE_FORMAT_VERSION = 1
_MAX_COOKIES = 100


def _load_structured_cookies(jar: requests.cookies.RequestsCookieJar, payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("version") != _COOKIE_FORMAT_VERSION:
        raise PlatformConfigurationError("stored cookie data has an unsupported format")
    items = payload.get("cookies")
    if not isinstance(items, list) or len(items) > _MAX_COOKIES:
        raise PlatformConfigurationError("stored cookie data is invalid")

    for item in items:
        if not isinstance(item, dict):
            raise PlatformConfigurationError("stored cookie data is invalid")
        name = item.get("name")
        value = item.get("value")
        domain = item.get("domain", "")
        path = item.get("path", "/")
        secure = item.get("secure", False)
        expires = item.get("expires")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(value, str)
            or not isinstance(domain, str)
            or not isinstance(path, str)
            or not isinstance(secure, bool)
            or (expires is not None and not isinstance(expires, int))
        ):
            raise PlatformConfigurationError("stored cookie data is invalid")
        jar.set(
            name,
            value,
            domain=domain,
            path=path or "/",
            secure=secure,
            expires=expires,
        )


def load_cookie_data(jar: requests.cookies.RequestsCookieJar, cookie_data: str) -> None:
    value = cookie_data.strip()
    if not value:
        return
    if value.startswith("{"):
        try:
            payload: object = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PlatformConfigurationError("stored cookie data is invalid JSON") from exc
        _load_structured_cookies(jar, payload)
        return

    parsed = SimpleCookie()
    try:
        parsed.load(value)
    except Exception as exc:
        raise PlatformConfigurationError("cookie header is invalid") from exc
    if not parsed or len(parsed) > _MAX_COOKIES:
        raise PlatformConfigurationError("cookie header is invalid")
    for morsel in parsed.values():
        jar.set(morsel.key, morsel.value)


def dump_cookie_data(jar: requests.cookies.RequestsCookieJar) -> str | None:
    cookies: list[dict[str, Any]] = []
    for cookie in jar:
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
            }
        )
    if not cookies:
        return None
    cookies.sort(key=lambda item: (item["domain"], item["path"], item["name"]))
    return json.dumps(
        {"version": _COOKIE_FORMAT_VERSION, "cookies": cookies},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
