from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import AppUser
from chaoxing_app.infrastructure.wechat import (
    WeChatClient,
    WeChatInvalidCodeError,
    WeChatRateLimitedError,
    WeChatSessionInfo,
)
from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings


def make_client(temp_dir: str) -> tuple[FastAPI, TestClient]:
    data_dir = Path(temp_dir) / "data"
    database_path = data_dir / "app.db"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{database_path.as_posix()}",
    )
    app = create_app(settings)
    return app, TestClient(app)


class FakeWeChatClient:
    def __init__(
        self,
        *,
        openid: str = "openid-fake-1",
        error: Exception | None = None,
    ) -> None:
        self._openid = openid
        self._error = error
        self.codes: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    def exchange_code(self, code: str) -> WeChatSessionInfo:
        self.codes.append(code)
        if self._error is not None:
            raise self._error
        return WeChatSessionInfo(openid=self._openid, session_key="session-key")


def wx_login(client: TestClient, *, code: str = "wx-code", nickname: str | None = None) -> dict:
    payload: dict[str, object] = {"code": code}
    if nickname is not None:
        payload["nickname"] = nickname
    return client.post("/api/v1/auth/wx/login", json=payload)


def test_wx_login_requires_configured_wechat_credentials() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            app.state.wechat_client = WeChatClient("", "")
            response = wx_login(client)
            assert response.status_code == 503
            assert response.json() == {"detail": "WeChat login is not configured"}


def test_wx_login_rejects_invalid_codes() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            app.state.wechat_client = FakeWeChatClient(
                error=WeChatInvalidCodeError("invalid code")
            )
            response = wx_login(client)
            assert response.status_code == 422
            assert response.json() == {"detail": "invalid WeChat login code"}


def test_wx_login_maps_rate_limit_and_client_failures() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            app.state.wechat_client = FakeWeChatClient(
                error=WeChatRateLimitedError("throttled")
            )
            assert wx_login(client).status_code == 429


def test_wx_login_creates_user_and_issues_session() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            fake = FakeWeChatClient(openid="openid-user-a")
            app.state.wechat_client = fake
            response = wx_login(client, nickname="Alice")
            assert response.status_code == 200
            body = response.json()
            assert body["user"]["nickname"] == "Alice"
            assert body["user"]["quotas"] == {"max_accounts": 3, "max_active_tasks": 1}
            assert body["csrf_token"]
            assert client.cookies.get("cx_session")

            # The same openid logs into the same user instead of creating a new one.
            with Session(app.state.engine) as db:
                assert len(list(db.scalars(select(AppUser)))) == 1
            second = wx_login(client, code="wx-code-2", nickname="Alice Two")
            assert second.status_code == 200
            assert second.json()["user"]["id"] == body["user"]["id"]
            assert second.json()["user"]["nickname"] == "Alice Two"

            me = client.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["kind"] == "app_user"
            assert me.json()["username"] is None
            assert me.json()["user"]["id"] == body["user"]["id"]
            assert me.json()["csrf_token"]

            # App-user sessions are refused by admin-only endpoints.
            assert client.get("/api/v1/settings").status_code == 403
            assert client.get("/api/v1/operations/health").status_code == 403
            assert client.get("/api/v1/app-users").status_code == 403


def test_wx_login_rejects_disabled_users() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            app.state.wechat_client = FakeWeChatClient(openid="openid-disabled")
            assert wx_login(client).status_code == 200
            with Session(app.state.engine) as db:
                user = db.scalar(select(AppUser))
                assert user is not None
                user.disabled = True
                db.commit()
            response = wx_login(client, code="wx-code-3")
            assert response.status_code == 403
            assert response.json() == {"detail": "account disabled"}


def test_wx_login_is_rate_limited_after_repeated_invalid_codes() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            app.state.wechat_client = FakeWeChatClient(
                error=WeChatInvalidCodeError("invalid code")
            )
            # The fifth recorded failure trips the block and returns 429 itself.
            for index in range(4):
                assert wx_login(client, code=f"bad-{index}").status_code == 422
            blocked = wx_login(client, code="bad-final")
            assert blocked.status_code == 429
            assert "Retry-After" in blocked.headers
            assert wx_login(client, code="bad-after-block").status_code == 429
