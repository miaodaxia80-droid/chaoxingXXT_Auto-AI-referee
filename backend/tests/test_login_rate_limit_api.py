from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from chaoxing_app.api import auth as auth_api
from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings

LOGIN_PATH = "/api/v1/auth/login"
CORRECT_PASSWORD = "correct-horse-battery-staple"
WRONG_PASSWORD = "incorrect-password"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data_dir = tmp_path / "data"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{(data_dir / 'app.db').as_posix()}",
        login_rate_limit_max_failures=3,
        login_rate_limit_window_seconds=60,
        login_rate_limit_block_seconds=17,
        login_rate_limit_max_buckets=100,
    )
    app = create_app(settings)
    with TestClient(app, client=("direct-peer", 50_000)) as test_client:
        setup = test_client.post(
            "/api/v1/auth/setup",
            json={"username": "admin", "password": CORRECT_PASSWORD},
        )
        assert setup.status_code == 201
        yield test_client


def login(
    client: TestClient,
    *,
    username: str = "admin",
    password: str = WRONG_PASSWORD,
    forwarded_for: str | None = None,
) -> Response:
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for is not None else None
    return cast(
        Response,
        client.post(
            LOGIN_PATH,
            headers=headers,
            json={"username": username, "password": password},
        ),
    )


def test_failure_threshold_returns_429_and_bounded_retry_after(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_verify = auth_api.password_service.verify
    verified_passwords: list[str] = []

    def tracked_verify(password_hash: str, password: str) -> bool:
        verified_passwords.append(password)
        return original_verify(password_hash, password)

    monkeypatch.setattr(auth_api.password_service, "verify", tracked_verify)

    assert login(client).status_code == 401
    assert login(client).status_code == 401

    threshold = login(client)
    assert threshold.status_code == 429
    assert threshold.json() == {"detail": "too many login attempts"}
    assert threshold.headers["Retry-After"] == "17"
    assert len(verified_passwords) == 3

    already_blocked = login(client)
    assert already_blocked.status_code == 429
    assert 1 <= int(already_blocked.headers["Retry-After"]) <= 17
    assert len(verified_passwords) == 3


def test_successful_login_clears_prior_failures(client: TestClient) -> None:
    assert login(client).status_code == 401
    assert login(client).status_code == 401

    successful = login(client, password=CORRECT_PASSWORD)
    assert successful.status_code == 200

    assert login(client).status_code == 401
    assert login(client).status_code == 401
    assert login(client).status_code == 429


def test_failure_buckets_are_isolated_by_username(client: TestClient) -> None:
    assert login(client, username="admin").status_code == 401
    assert login(client, username="missing-user").status_code == 401
    assert login(client, username="missing-user").status_code == 401

    missing_user_blocked = login(client, username="missing-user")
    assert missing_user_blocked.status_code == 429

    admin_success = login(client, username="admin", password=CORRECT_PASSWORD)
    assert admin_success.status_code == 200


def test_x_forwarded_for_does_not_create_a_new_source_bucket(client: TestClient) -> None:
    assert login(client, forwarded_for="198.51.100.10").status_code == 401
    assert login(client, forwarded_for="203.0.113.20").status_code == 401

    blocked = login(client, forwarded_for="192.0.2.30")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "17"
