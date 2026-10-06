from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest
import requests

import chaoxing_app.infrastructure.egress as egress
from chaoxing_app.infrastructure.egress import (
    RotatingEgressSession,
    random_proxy_session_factory,
)
from chaoxing_app.settings import AppSettings

POOL = (
    "http://100.64.0.1:10811",
    "http://100.64.0.2:10811",
    "http://100.64.0.3:10811",
)


def _fake_response() -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    return response


def _clear_probe_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(egress, "_PROBE_CACHE", {})


def test_empty_pool_falls_back_to_default_session_factory() -> None:
    factory = random_proxy_session_factory(())
    assert factory is requests.Session
    session = factory()
    assert session.trust_env is True
    assert session.proxies == {}


def test_factory_probes_pool_and_keeps_only_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_probe_cache(monkeypatch)
    monkeypatch.setattr(egress, "probe_proxy", lambda proxy: proxy != POOL[2])
    session = random_proxy_session_factory(POOL)()
    assert isinstance(session, RotatingEgressSession)
    assert session.trust_env is False
    assert set(session._egress_proxies) == {POOL[0], POOL[1]}


def test_factory_falls_back_to_full_pool_when_all_probes_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_probe_cache(monkeypatch)
    monkeypatch.setattr(egress, "probe_proxy", lambda proxy: False)
    session = random_proxy_session_factory(POOL)()
    assert set(session._egress_proxies) == set(POOL)


def test_session_sticks_to_one_proxy_until_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RotatingEgressSession(POOL[:2])
    used_proxies: list[str] = []
    first_failure = True

    def fake_send(
        self: requests.Session,
        request: requests.PreparedRequest,
        **kwargs: Any,
    ) -> requests.Response:
        proxies = kwargs.get("proxies")
        assert isinstance(proxies, dict)
        proxy = proxies["http"]
        used_proxies.append(proxy)
        nonlocal first_failure
        if first_failure:
            first_failure = False
            raise requests.exceptions.ConnectionError("connect failed")
        return _fake_response()

    monkeypatch.setattr(requests.Session, "send", fake_send)
    prepared = requests.Request("GET", "https://mooc1.chaoxing.com/x").prepare()
    response = session.send(prepared)
    assert response.status_code == 200
    assert len(used_proxies) == 2
    assert used_proxies[0] in POOL[:2]
    assert used_proxies[1] in POOL[:2]
    assert used_proxies[0] != used_proxies[1]

    session.send(prepared)
    assert len(used_proxies) == 3
    assert used_proxies[2] == used_proxies[1]


def test_session_rotates_again_when_switched_proxy_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RotatingEgressSession(POOL[:3])
    used_proxies: list[str] = []
    failing = {POOL[0], POOL[1]}

    def fake_send(
        self: requests.Session,
        request: requests.PreparedRequest,
        **kwargs: Any,
    ) -> requests.Response:
        proxies = kwargs.get("proxies")
        assert isinstance(proxies, dict)
        proxy = proxies["http"]
        used_proxies.append(proxy)
        if proxy in failing:
            raise requests.exceptions.ConnectionError("connect failed")
        return _fake_response()

    monkeypatch.setattr(requests.Session, "send", fake_send)
    prepared = requests.Request("GET", "https://mooc1.chaoxing.com/x").prepare()
    response = session.send(prepared)
    assert response.status_code == 200
    assert used_proxies[-1] == POOL[2]

    session.send(prepared)
    assert used_proxies[-1] == POOL[2]
    assert used_proxies.count(POOL[2]) == 2


def test_session_raises_when_every_proxy_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    session = RotatingEgressSession(POOL[:2])
    used_proxies: list[str] = []

    def fake_send(
        self: requests.Session,
        request: requests.PreparedRequest,
        **kwargs: Any,
    ) -> requests.Response:
        proxies = kwargs.get("proxies")
        assert isinstance(proxies, dict)
        used_proxies.append(proxies["http"])
        raise requests.exceptions.ConnectionError("connect failed")

    monkeypatch.setattr(requests.Session, "send", fake_send)
    prepared = requests.Request("GET", "https://mooc1.chaoxing.com/x").prepare()
    with pytest.raises(requests.exceptions.ConnectionError):
        session.send(prepared)
    assert len(used_proxies) == len(POOL[:2])
    assert set(used_proxies) == set(POOL[:2])


def test_session_with_empty_pool_sends_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    session = RotatingEgressSession(())
    seen: list[dict[str, str] | None] = []

    def fake_send(
        self: requests.Session,
        request: requests.PreparedRequest,
        **kwargs: Any,
    ) -> requests.Response:
        seen.append(kwargs.get("proxies"))
        return _fake_response()

    monkeypatch.setattr(requests.Session, "send", fake_send)
    session.send(requests.Request("GET", "https://mooc1.chaoxing.com/x").prepare())
    assert seen == [None]


def test_probe_proxy_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_probe_cache(monkeypatch)
    connects = 0

    def fake_tcp_connect(proxy: str, timeout: float) -> bool:
        nonlocal connects
        connects += 1
        return True

    monkeypatch.setattr(egress, "_tcp_connect", fake_tcp_connect)
    assert egress.probe_proxy(POOL[0]) is True
    assert egress.probe_proxy(POOL[0]) is True
    assert connects == 1
    assert egress.probe_proxy(POOL[1]) is True
    assert connects == 2


def test_probe_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_probe_cache(monkeypatch)
    monkeypatch.setattr(egress, "PROBE_CACHE_TTL_SECONDS", 0)
    connects = 0

    def fake_tcp_connect(proxy: str, timeout: float) -> bool:
        nonlocal connects
        connects += 1
        return True

    monkeypatch.setattr(egress, "_tcp_connect", fake_tcp_connect)
    assert egress.probe_proxy(POOL[0]) is True
    assert egress.probe_proxy(POOL[0]) is True
    assert connects == 2


def test_tcp_connect_hits_proxy_port(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[tuple[str, int]] = []

    def fake_create_connection(
        address: tuple[str, int],
        timeout: float | None = None,
    ) -> object:
        opened.append(address)
        if address == ("100.64.0.1", 10811):
            return nullcontext()
        raise OSError("connection refused")

    monkeypatch.setattr(egress.socket, "create_connection", fake_create_connection)
    assert egress._tcp_connect("http://100.64.0.1:10811", 1.0) is True
    assert egress._tcp_connect("http://100.64.0.3:10811", 1.0) is False
    assert opened == [("100.64.0.1", 10811), ("100.64.0.3", 10811)]


def test_settings_parse_comma_separated_egress_proxies() -> None:
    settings = AppSettings(
        environment="test",
        egress_proxies=", ".join(POOL),
    )
    assert settings.egress_proxies == POOL


def test_settings_accept_json_list_egress_proxies() -> None:
    settings = AppSettings(environment="test", egress_proxies=list(POOL))
    assert settings.egress_proxies == POOL


def test_settings_deduplicate_and_normalize_egress_proxies() -> None:
    settings = AppSettings(
        environment="test",
        egress_proxies="http://100.64.0.1:10811,http://100.64.0.1:10811/",
    )
    assert settings.egress_proxies == (POOL[0],)


def test_settings_reject_invalid_egress_proxy() -> None:
    with pytest.raises(ValueError):
        AppSettings(environment="test", egress_proxies="ftp://100.64.0.1:10811")
