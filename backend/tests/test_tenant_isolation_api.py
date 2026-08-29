from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import ChapterStatus
from chaoxing_app.infrastructure.db.models import Account, StudyTask
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


def create_account(client: TestClient, csrf: str, username: str) -> int:
    response = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={"username": username, "password": "secret-password", "remark": username},
    )
    assert response.status_code == 201
    return response.json()["id"]


def create_task(client: TestClient, csrf: str, account_id: int, course_id: str) -> str:
    response = client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account_id,
            "course_id": course_id,
            "class_id": f"class-{course_id}",
            "cpi": f"cpi-{course_id}",
            "course_title": f"Course {course_id}",
            "chapters": [
                {"chapter_id": f"ch-{course_id}", "title": "Chapter", "position": 0}
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_app_user_cannot_access_another_users_accounts() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client_a = make_client(temp_dir)
        with client_a:
            csrf_a = login_app_user(client_a, app, "openid-a")
            account_id = create_account(client_a, csrf_a, "chaoxing-user-a")
            assert [item["id"] for item in client_a.get("/api/v1/accounts").json()] == [
                account_id
            ]

        with client_a:
            csrf_b = login_app_user(client_a, app, "openid-b")
            assert client_a.get("/api/v1/accounts").json() == []
            patch = client_a.patch(
                f"/api/v1/accounts/{account_id}",
                headers={"X-CSRF-Token": csrf_b},
                json={"remark": "hijack"},
            )
            assert patch.status_code == 404
            delete = client_a.delete(
                f"/api/v1/accounts/{account_id}",
                headers={"X-CSRF-Token": csrf_b},
            )
            assert delete.status_code == 404
            discover = client_a.post(
                f"/api/v1/accounts/{account_id}/courses/discover",
                headers={"X-CSRF-Token": csrf_b},
            )
            assert discover.status_code == 404


def test_app_user_cannot_access_another_users_tasks_and_events() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf_a = login_app_user(client, app, "openid-a")
            account_id = create_account(client, csrf_a, "chaoxing-user-a")
            task_id = create_task(client, csrf_a, account_id, "course-1")

            csrf_b = login_app_user(client, app, "openid-b")
            assert client.get("/api/v1/tasks").json() == []
            assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404
            cancel = client.post(
                f"/api/v1/tasks/{task_id}/cancel",
                headers={"X-CSRF-Token": csrf_b},
            )
            assert cancel.status_code == 404
            assert client.get(f"/api/v1/tasks/{task_id}/events").status_code == 404
            assert client.get("/api/v1/events").json() == []
            assert client.get("/api/v1/operations/interventions").json() == []

        # An admin still sees every account regardless of ownership.
        with client:
            bootstrap_admin(client)
            accounts = client.get("/api/v1/accounts").json()
            assert [item["id"] for item in accounts] == [account_id]
            tasks = client.get("/api/v1/tasks").json()
            assert [item["id"] for item in tasks] == [task_id]
            assert client.get(f"/api/v1/tasks/{task_id}").status_code == 200
            assert client.get("/api/v1/events").json() != []
            assert client.get(f"/api/v1/tasks/{task_id}/events").json() != []


def test_interventions_are_scoped_to_owner() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf_a = login_app_user(client, app, "openid-a")
            account_id = create_account(client, csrf_a, "chaoxing-user-a")
            task_id = create_task(client, csrf_a, account_id, "course-1")

            # Seed a failed chapter directly so the intervention projection fires.
            with Session(app.state.engine) as db:
                task = db.get(StudyTask, task_id)
                assert task is not None
                task.status = "succeeded"
                chapter = task.chapters[0]
                chapter.status = ChapterStatus.FAILED.value
                chapter_id = chapter.id
                db.commit()

            csrf_b = login_app_user(client, app, "openid-b")
            assert client.get("/api/v1/operations/interventions").json() == []
            resolve = client.post(
                "/api/v1/operations/interventions/resolve",
                headers={"X-CSRF-Token": csrf_b},
                json={"ids": [chapter_id]},
            )
            assert resolve.status_code == 200
            body = resolve.json()
            assert body["resolved"] == 0
            assert body["not_actionable"] == 1

            csrf_a2 = login_app_user(client, app, "openid-a")
            own = client.get("/api/v1/operations/interventions").json()
            assert [item["task_id"] for item in own] == [task_id]
            own_resolve = client.post(
                "/api/v1/operations/interventions/resolve",
                headers={"X-CSRF-Token": csrf_a2},
                json={"ids": [chapter_id]},
            )
            assert own_resolve.status_code == 200
            assert own_resolve.json()["resolved"] == 1


def test_admin_can_inspect_app_users() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            login_app_user(client, app, "openid-a")
            admin_csrf = bootstrap_admin(client)
            users = client.get("/api/v1/app-users").json()
            assert len(users) == 1
            assert users[0]["openid"] == "openid-a"
            assert users[0]["account_count"] == 0
            user_id = users[0]["id"]
            update = client.patch(
                f"/api/v1/app-users/{user_id}",
                headers={"X-CSRF-Token": admin_csrf},
                json={"disabled": True, "quotas": {"max_accounts": 5}},
            )
            assert update.status_code == 200
            assert update.json()["disabled"] is True
            assert update.json()["quotas"]["max_accounts"] == 5
            assert update.json()["quotas"]["max_active_tasks"] == 1

            with Session(app.state.engine) as db:
                account = db.get(Account, 1)
                assert account is None  # sanity: no accounts were created
