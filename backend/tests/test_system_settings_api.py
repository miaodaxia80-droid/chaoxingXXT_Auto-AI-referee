from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings


def make_settings(temp_dir: str) -> AppSettings:
    data_dir = Path(temp_dir) / "data"
    return AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{(data_dir / 'settings.db').as_posix()}",
    )


def login(client: TestClient, *, setup: bool = True) -> str:
    if setup:
        response = client.post(
            "/api/v1/auth/setup",
            json={"username": "admin", "password": "correct-horse-battery-staple"},
        )
        assert response.status_code == 201
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_settings_require_auth_default_to_all_day_and_never_return_secrets() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(make_settings(temp_dir))
        with TestClient(app) as client:
            assert client.get("/api/v1/settings").status_code == 401
            login(client)

            response = client.get("/api/v1/settings")

            assert response.status_code == 200
            body = response.json()
            assert set(body) == {
                "worker_enabled",
                "run_window_enabled",
                "run_window_start",
                "run_window_end",
                "timezone",
                "event_retention_days",
                "updated_at",
            }
            assert body["worker_enabled"] is True
            assert body["run_window_enabled"] is False
            assert body["run_window_start"] == "00:00"
            assert body["run_window_end"] == "00:00"
            assert body["timezone"] == "Asia/Shanghai"
            assert body["event_retention_days"] == 30


def test_settings_update_requires_csrf_validates_and_persists_across_restart() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        settings = make_settings(temp_dir)
        app = create_app(settings)
        with TestClient(app) as client:
            csrf = login(client)
            payload = {
                "worker_enabled": True,
                "run_window_enabled": True,
                "run_window_start": "22:30",
                "run_window_end": "06:15",
                "timezone": "Asia/Shanghai",
                "event_retention_days": 45,
            }
            assert client.patch("/api/v1/settings", json=payload).status_code == 403
            invalid = client.patch(
                "/api/v1/settings",
                headers={"X-CSRF-Token": csrf},
                json={"run_window_start": "9:00"},
            )
            assert invalid.status_code == 422
            invalid_zone = client.patch(
                "/api/v1/settings",
                headers={"X-CSRF-Token": csrf},
                json={"timezone": "Invalid/Zone"},
            )
            assert invalid_zone.status_code == 422

            updated = client.patch(
                "/api/v1/settings",
                headers={"X-CSRF-Token": csrf},
                json=payload,
            )
            assert updated.status_code == 200
            assert updated.json()["run_window_start"] == "22:30"
            assert updated.json()["run_window_end"] == "06:15"
            assert updated.json()["event_retention_days"] == 45

        restarted = create_app(settings)
        with TestClient(restarted) as client:
            login(client, setup=False)
            persisted = client.get("/api/v1/settings")
            assert persisted.status_code == 200
            assert persisted.json()["run_window_start"] == "22:30"
            assert persisted.json()["run_window_end"] == "06:15"
            assert persisted.json()["event_retention_days"] == 45
