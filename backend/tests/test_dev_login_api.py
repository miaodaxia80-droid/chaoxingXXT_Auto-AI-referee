from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import AppUser
from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings


def make_client(
    temp_dir: str,
    *,
    dev_login_enabled: bool = False,
) -> tuple[FastAPI, TestClient]:
    data_dir = Path(temp_dir) / "data"
    database_path = data_dir / "app.db"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{database_path.as_posix()}",
        dev_login_enabled=dev_login_enabled,
    )
    app = create_app(settings)
    return app, TestClient(app)


def dev_login(client: TestClient, token: str = "browser-preview") -> dict:
    return client.post(
        "/api/v1/auth/dev-login",
        json={"token": token, "nickname": "预览用户"},
    )


def test_dev_login_is_hidden_when_disabled() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            assert dev_login(client).status_code == 404


def test_dev_login_issues_an_app_user_session() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir, dev_login_enabled=True)
        with client:
            first = dev_login(client)
            assert first.status_code == 200
            body = first.json()
            assert body["user"]["nickname"] == "预览用户"
            assert body["csrf_token"]
            assert client.cookies.get("cx_session")

            with Session(app.state.engine) as db:
                users = list(db.scalars(select(AppUser)))
                assert len(users) == 1
                assert users[0].openid == "dev:browser-preview"

            # Same token logs into the same user instead of creating another.
            second = dev_login(client)
            assert second.status_code == 200
            assert second.json()["user"]["id"] == body["user"]["id"]

            me = client.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["kind"] == "app_user"
            assert me.json()["user"]["id"] == body["user"]["id"]

            # Normal app-user quotas apply to dev users as well.
            accounts = client.get("/api/v1/accounts")
            assert accounts.status_code == 200
            assert accounts.json() == []


def test_dev_login_rejects_disabled_dev_users() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir, dev_login_enabled=True)
        with client:
            assert dev_login(client).status_code == 200
            with Session(app.state.engine) as db:
                user = db.scalar(select(AppUser))
                assert user is not None
                user.disabled = True
                db.commit()
            assert dev_login(client).status_code == 403
