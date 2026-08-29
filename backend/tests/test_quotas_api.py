from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import AppUser, StudyTask
from chaoxing_app.infrastructure.wechat import WeChatSessionInfo
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
    def __init__(self, openid: str) -> None:
        self._openid = openid

    @property
    def configured(self) -> bool:
        return True

    def exchange_code(self, code: str) -> WeChatSessionInfo:
        return WeChatSessionInfo(openid=self._openid, session_key="session-key")


def bootstrap_admin(client: TestClient) -> str:
    setup = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert setup.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    return login.json()["csrf_token"]


def login_app_user(client: TestClient, app: FastAPI, openid: str) -> str:
    app.state.wechat_client = FakeWeChatClient(openid)
    response = client.post("/api/v1/auth/wx/login", json={"code": "code"})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def set_quotas(app: FastAPI, openid: str, quotas: dict[str, int]) -> int:
    with Session(app.state.engine) as db:
        user = db.scalar(select(AppUser).where(AppUser.openid == openid))
        assert user is not None
        user.quotas = quotas
        db.commit()
        return user.id


def grant_task_credits(app: FastAPI, openid: str, credits: int = 10) -> None:
    """Test helper for the entitlement gate: grant count-card credits directly."""
    with Session(app.state.engine) as db:
        user = db.scalar(select(AppUser).where(AppUser.openid == openid))
        assert user is not None
        user.task_credits = credits
        db.commit()


def create_account(client: TestClient, csrf: str, username: str) -> int:
    response = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={"username": username, "password": "secret-password"},
    )
    return response


def create_task(client: TestClient, csrf: str, account_id: int, course_id: str):
    return client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "course_id": course_id,
            "class_id": f"class-{course_id}",
            "cpi": f"cpi-{course_id}",
            "course_title": f"Course {course_id}",
        },
    )


def test_account_quota_blocks_additional_accounts() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf = login_app_user(client, app, "openid-quota")
            set_quotas(app, "openid-quota", {"max_accounts": 1, "max_active_tasks": 5})

            first = create_account(client, csrf, "chaoxing-1")
            assert first.status_code == 201
            second = create_account(client, csrf, "chaoxing-2")
            assert second.status_code == 409
            assert second.json() == {"detail": "account quota exceeded"}


def test_import_rows_beyond_quota_fail_with_error_code() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf = login_app_user(client, app, "openid-import")
            set_quotas(app, "openid-import", {"max_accounts": 1, "max_active_tasks": 5})

            csv_body = (
                "username,password,remark\n"
                "chaoxing-a,secret,first\n"
                "chaoxing-b,secret,second\n"
            )
            response = client.post(
                "/api/v1/accounts/import",
                headers={"X-CSRF-Token": csrf, "Content-Type": "text/csv"},
                content=csv_body.encode("utf-8"),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["created"] == 1
            assert body["invalid"] == 1
            assert [row["error_code"] for row in body["results"]] == [
                None,
                "account_quota_exceeded",
            ]


def test_active_task_quota_blocks_additional_tasks() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf = login_app_user(client, app, "openid-tasks")
            set_quotas(app, "openid-tasks", {"max_accounts": 3, "max_active_tasks": 1})
            grant_task_credits(app, "openid-tasks")

            account = create_account(client, csrf, "chaoxing-1").json()["id"]
            first = create_task(client, csrf, account, "course-1")
            assert first.status_code == 201
            second = create_task(client, csrf, account, "course-2")
            assert second.status_code == 409
            assert second.json() == {"detail": "task quota exceeded"}

            # A finished task frees the slot again.
            task_id = first.json()["id"]
            with Session(app.state.engine) as db:
                db.get(StudyTask, task_id).status = "succeeded"
                db.commit()
            third = create_task(client, csrf, account, "course-3")
            assert third.status_code == 201


def test_bulk_create_reports_quota_error_code_per_item() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf = login_app_user(client, app, "openid-bulk")
            set_quotas(app, "openid-bulk", {"max_accounts": 3, "max_active_tasks": 1})
            grant_task_credits(app, "openid-bulk")

            account = create_account(client, csrf, "chaoxing-1").json()["id"]
            response = client.post(
                "/api/v1/tasks/bulk-create",
                headers={"X-CSRF-Token": csrf},
                json={
                    "tasks": [
                        {
                            "account_id": account,
                            "course_id": f"course-{index}",
                            "class_id": f"class-{index}",
                            "cpi": f"cpi-{index}",
                            "course_title": f"Course {index}",
                        }
                        for index in range(2)
                    ]
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["created"] == 1
            assert body["failed"] == 1
            assert [item["error_code"] for item in body["results"]] == [
                None,
                "task_quota_exceeded",
            ]


def test_admin_quota_updates_take_effect() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            user_csrf = login_app_user(client, app, "openid-admin-quota")
            user_id = set_quotas(
                app, "openid-admin-quota", {"max_accounts": 1, "max_active_tasks": 5}
            )
            assert create_account(client, user_csrf, "chaoxing-1").status_code == 201

            admin_csrf = bootstrap_admin(client)
            update = client.patch(
                f"/api/v1/app-users/{user_id}",
                headers={"X-CSRF-Token": admin_csrf},
                json={"quotas": {"max_accounts": 2}},
            )
            assert update.status_code == 200
            assert update.json()["quotas"] == {"max_accounts": 2, "max_active_tasks": 5}

            # Back to an app-user session: the raised quota now admits one more account.
            user_csrf = login_app_user(client, app, "openid-admin-quota")
            assert create_account(client, user_csrf, "chaoxing-2").status_code == 201
            assert create_account(client, user_csrf, "chaoxing-3").status_code == 409

            # The app user cannot manage other users or self-serve quotas.
            forbidden = client.patch(
                f"/api/v1/app-users/{user_id}",
                headers={"X-CSRF-Token": user_csrf},
                json={"quotas": {"max_accounts": 99}},
            )
            assert forbidden.status_code == 403
