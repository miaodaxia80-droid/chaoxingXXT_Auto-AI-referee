"""Egress proxy selection for Chaoxing platform traffic.

Chaoxing blocks cloud datacenter IPs, so outbound traffic must leave through
a residential egress. Operators configure CX_EGRESS_PROXIES as a pool of
HTTP proxies, one per Tailscale exit node (each running tinyproxy).

Every fresh session probes the pool with a short TCP connect, keeps only
reachable exits, and pins the session to one random healthy exit so
consecutive tasks spread across exit nodes. A session stays on its chosen
egress (the account login state is bound to the egress IP, and switching
mid-task trips the platform's risk control) but rotates to the next exit and
retries when a request dies with a connection error.
"""

from __future__ import annotations

import logging
import random
import socket
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

ProxySessionFactory = Callable[[], requests.Session]

PROBE_TIMEOUT_SECONDS = 2.0
PROBE_CACHE_TTL_SECONDS = 30.0
_PROBE_CACHE: dict[str, tuple[float, bool]] = {}


def clear_probe_cache() -> None:
    _PROBE_CACHE.clear()


def probe_proxy(proxy: str, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """Return whether the proxy accepts TCP connections, cached briefly."""
    now = time.monotonic()
    cached = _PROBE_CACHE.get(proxy)
    if cached is not None and now - cached[0] < PROBE_CACHE_TTL_SECONDS:
        return cached[1]
    alive = _tcp_connect(proxy, timeout)
    _PROBE_CACHE[proxy] = (now, alive)
    return alive


def _tcp_connect(proxy: str, timeout: float) -> bool:
    parsed = urlsplit(proxy)
    host = parsed.hostname
    if host is None:
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class RotatingEgressSession(requests.Session):
    """Session pinned to a random pool egress, failing over on connection errors."""

    def __init__(self, proxies: tuple[str, ...]) -> None:
        super().__init__()
        # The pool decides the egress; ignore HTTP(S)_PROXY env vars so a
        # fixed default exit cannot override the per-task random selection.
        self.trust_env = False
        self._egress_proxies = list(proxies)
        self._active_index = 0
        random.shuffle(self._egress_proxies)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        pool = self._egress_proxies
        if not pool:
            return super().send(request, **kwargs)
        last_error: requests.ConnectionError | None = None
        for offset in range(len(pool)):
            proxy = pool[(self._active_index + offset) % len(pool)]
            kwargs["proxies"] = {"http": proxy, "https": proxy}
            try:
                response = super().send(request, **kwargs)
            except requests.exceptions.ConnectionError as exc:
                last_error = exc
                continue
            self._active_index = (self._active_index + offset) % len(pool)
            return response
        assert last_error is not None
        raise last_error


def random_proxy_session_factory(proxies: tuple[str, ...]) -> ProxySessionFactory:
    """Return a session factory that probes the pool before each session.

    Each call drops unreachable exits and builds a ``RotatingEgressSession``
    over the healthy remainder. An empty pool falls back to the default
    ``requests.Session``, which honors ``HTTP_PROXY``/``HTTPS_PROXY`` from
    the process environment.
    """
    if not proxies:
        return requests.Session

    def factory() -> requests.Session:
        healthy = tuple(proxy for proxy in proxies if probe_proxy(proxy))
        if not healthy:
            logger.warning(
                "all %d egress proxies unreachable, falling back to the full pool",
                len(proxies),
            )
            healthy = proxies
        return RotatingEgressSession(healthy)

    return factory
