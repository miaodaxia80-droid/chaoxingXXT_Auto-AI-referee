import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import Event, StudyTask
from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings


def make_client(temp_dir: str):
    data_dir = Path(temp_dir) / "data"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{(data_dir / 'app.db').as_posix()}",
    )
    app = create_app(settings)
    return app, TestClient(app)


def bootstrap(client: TestClient) -> tuple[str, int]:
    assert (
        client.post(
            "/api/v1/auth/setup",
            json={"username": "admin", "password": "correct-horse-battery-staple"},
        ).status_code
        == 201
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]
    account = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={"username": "13800138000", "password": "upstream-password"},
    )
    assert account.status_code == 201
    return csrf, account.json()["id"]


def task_payload(account_id: int, index: int) -> dict[str, object]:
    return {
        "account_id": account_id,
        "course_id": f"course-{index}",
        "class_id": f"class-{index}",
        "cpi": f"cpi-{index}",
        "course_title": f"Course {index}",
        "chapters": [
            {
                "chapter_id": f"chapter-{index}",
                "title": f"Chapter {index}",
                "position": 0,
            }
        ],
    }


def create_task(client: TestClient, csrf: str, account_id: int, index: int) -> str:
    response = client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": csrf},
        json=task_payload(account_id, index),
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_bulk_create_commits_successful_items_and_reports_each_failure() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf, account_id = bootstrap(client)
            payloads = [
                task_payload(account_id, 1),
                task_payload(999_999, 2),
                task_payload(account_id, 1),
                task_payload(account_id, 3),
            ]
            unauthorized = client.post(
                "/api/v1/tasks/bulk-create", json={"tasks": payloads}
            )
            assert unauthorized.status_code == 403

            response = client.post(
                "/api/v1/tasks/bulk-create",
                headers={"X-CSRF-Token": csrf},
                json={"tasks": payloads},
            )
            assert response.status_code == 200
            body = response.json()
            assert (body["total"], body["created"], body["failed"]) == (4, 2, 2)
            assert [result["status"] for result in body["results"]] == [
                "created",
                "failed",
                "failed",
                "created",
            ]
            assert body["results"][1]["error_code"] == "account_not_found"
            assert body["results"][2]["error_code"] == "duplicate_active_task"
            assert body["results"][0]["task"]["chapter_total"] == 1

            with Session(app.state.engine) as session:
                assert list(
                    session.scalars(select(StudyTask.course_id).order_by(StudyTask.course_id))
                ) == ["course-1", "course-3"]
                assert session.scalar(select(func.count()).select_from(Event)) == 2


def test_bulk_actions_keep_valid_transitions_when_other_items_fail() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf, account_id = bootstrap(client)
            first = create_task(client, csrf, account_id, 1)
            terminal = create_task(client, csrf, account_id, 2)
            with Session(app.state.engine) as session:
                task = session.get(StudyTask, terminal)
                assert task is not None
                task.status = "succeeded"
                session.commit()

            headers = {"X-CSRF-Token": csrf}
            paused = client.post(
                "/api/v1/tasks/bulk-action",
                headers=headers,
                json={"action": "pause", "task_ids": [first, terminal, "missing"]},
            )
            assert paused.status_code == 200
            body = paused.json()
            assert (body["succeeded"], body["failed"]) == (1, 2)
            assert body["results"][0]["task"]["status"] == "paused"
            assert body["results"][1]["error_code"] == "invalid_state"
            assert body["results"][2]["error_code"] == "task_not_found"

            resumed = client.post(
                "/api/v1/tasks/bulk-action",
                headers=headers,
                json={"action": "resume", "task_ids": [first]},
            )
            assert resumed.json()["results"][0]["task"]["status"] == "queued"
            canceled = client.post(
                "/api/v1/tasks/bulk-action",
                headers=headers,
                json={"action": "cancel", "task_ids": [first]},
            )
            assert canceled.json()["results"][0]["task"]["status"] == "canceled"

            with Session(app.state.engine) as session:
                persisted = session.get(StudyTask, first)
                assert persisted is not None
                assert persisted.status == "canceled"
                kinds = list(
                    session.scalars(
                        select(Event.kind).where(Event.task_id == first).order_by(Event.id)
                    )
                )
                assert kinds == ["task.queued", "task.paused", "task.resumed", "task.canceled"]


def test_bulk_delete_and_history_cleanup_never_delete_active_tasks() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf, account_id = bootstrap(client)
            active = create_task(client, csrf, account_id, 1)
            first_terminal = create_task(client, csrf, account_id, 2)
            second_terminal = create_task(client, csrf, account_id, 3)
            with Session(app.state.engine) as session:
                for task_id, task_status in (
                    (first_terminal, "succeeded"),
                    (second_terminal, "failed"),
                ):
                    task = session.get(StudyTask, task_id)
                    assert task is not None
                    task.status = task_status
                session.commit()

            headers = {"X-CSRF-Token": csrf}
            deleted = client.request(
                "DELETE",
                "/api/v1/tasks/bulk",
                headers=headers,
                json={"task_ids": [active, first_terminal, "missing"]},
            )
            assert deleted.status_code == 200
            body = deleted.json()
            assert (body["deleted"], body["failed"]) == (1, 2)
            assert body["results"][0]["error_code"] == "active_task"
            assert body["results"][1]["status"] == "deleted"
            assert body["results"][2]["error_code"] == "task_not_found"

            with Session(app.state.engine) as session:
                assert session.get(StudyTask, active) is not None
                assert session.get(StudyTask, first_terminal) is None
                assert session.scalar(
                    select(func.count()).select_from(Event).where(Event.task_id == first_terminal)
                ) == 0

            cleaned = client.delete("/api/v1/tasks/history", headers=headers)
            assert cleaned.status_code == 200
            assert cleaned.json() == {"deleted": 1}
            with Session(app.state.engine) as session:
                assert session.get(StudyTask, active) is not None
                assert session.get(StudyTask, second_terminal) is None


def test_bulk_requests_reject_duplicate_task_ids() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf, _account_id = bootstrap(client)
            headers = {"X-CSRF-Token": csrf}
            action = client.post(
                "/api/v1/tasks/bulk-action",
                headers=headers,
                json={"action": "pause", "task_ids": ["same", "same"]},
            )
            assert action.status_code == 422
            deleted = client.request(
                "DELETE",
                "/api/v1/tasks/bulk",
                headers=headers,
                json={"task_ids": ["same", "same"]},
            )
            assert deleted.status_code == 422
