# -*- coding: utf-8 -*-
import os.path

import requests

from api.config import GlobalConst as gc


def parse_cookie_string(buffer: str) -> dict:
    cookies = {}
    for item in buffer.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        cookies[k.strip()] = v.strip()
    return cookies


def dump_cookie_string(session: requests.Session) -> str:
    return ";".join(f"{k}={v}" for k, v in session.cookies.items())


def save_cookies(session: requests.Session, path: str = gc.COOKIES_PATH):
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_cookie_string(session))


def use_cookies(path: str = gc.COOKIES_PATH) -> dict:
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return parse_cookie_string(f.read().strip())
