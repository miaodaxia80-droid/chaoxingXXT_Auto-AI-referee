import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import TaskStatus
from chaoxing_app.infrastructure.db.models import Event, StudyTask, TaskChapter
from chaoxing_app.infrastructure.db.task_commands import TaskCommandService
from chaoxing_app.infrastructure.db.tasks import StudyTaskRepository
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


def create_task(client: TestClient) -> tuple[str, str]:
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
    created = client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account.json()["id"],
            "course_id": "course-1",
            "class_id": "class-1",
            "cpi": "cpi-1",
            "course_title": "Course One",
            "chapters": [
                {"chapter_id": "chapter-1", "title": "Chapter One", "position": 0}
            ],
        },
    )
    assert created.status_code == 201
    return csrf, created.json()["id"]


def test_queued_task_pause_resume_and_cancel_are_durable_and_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf, task_id = create_task(client)
            headers = {"X-CSRF-Token": csrf}

            paused = client.post(f"/api/v1/tasks/{task_id}/pause", headers=headers)
            assert paused.status_code == 200
            assert paused.json()["status"] == "paused"
            assert paused.json()["desired_state"] == "pause"

            repeated = client.post(f"/api/v1/tasks/{task_id}/pause", headers=headers)
            assert repeated.status_code == 200
            resumed = client.post(f"/api/v1/tasks/{task_id}/resume", headers=headers)
            assert resumed.status_code == 200
            assert resumed.json()["status"] == "queued"

            canceled = client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers)
            assert canceled.status_code == 200
            assert canceled.json()["status"] == "canceled"
            assert canceled.json()["chapters"][0]["status"] == "canceled"
            repeated_cancel = client.post(
                f"/api/v1/tasks/{task_id}/cancel", headers=headers
            )
            assert repeated_cancel.status_code == 200

            with Session(app.state.engine) as session:
                kinds = list(
                    session.scalars(
                        select(Event.kind).where(Event.task_id == task_id).order_by(Event.id)
                    )
                )
                assert kinds == ["task.queued", "task.paused", "task.resumed", "task.canceled"]
                chapter = session.scalar(
                    select(TaskChapter).where(TaskChapter.task_id == task_id)
                )
                assert chapter is not None
                assert chapter.finished_at is not None


def test_running_task_uses_cooperative_pause_resume_and_cancel_requests() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf, task_id = create_task(client)
            with Session(app.state.engine) as session:
                task = session.get(StudyTask, task_id)
                assert task is not None
                task.status = "running"
                session.commit()

            headers = {"X-CSRF-Token": csrf}
            pause = client.post(f"/api/v1/tasks/{task_id}/pause", headers=headers)
            assert pause.status_code == 200
            assert pause.json()["status"] == "pause_requested"
            resume = client.post(f"/api/v1/tasks/{task_id}/resume", headers=headers)
            assert resume.status_code == 200
            assert resume.json()["status"] == "running"
            cancel = client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers)
            assert cancel.status_code == 200
            assert cancel.json()["status"] == "cancel_requested"
            assert cancel.json()["desired_state"] == "cancel"


def test_resume_reloads_task_after_a_concurrent_command_commits() -> None:
    """A command response must describe the state it actually committed."""

    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            _csrf, task_id = create_task(client)
            with Session(app.state.engine) as setup_session:
                task = setup_session.get(StudyTask, task_id)
                assert task is not None
                task.status = TaskStatus.RUNNING.value
                task.desired_state = "run"
                task.updated_at = datetime.now(UTC)
                setup_session.commit()

            # Session A has an intentionally stale identity-map entry.  Session
            # B then commits the command that Session A must observe.
            with Session(app.state.engine) as stale_session:
                stale_task = StudyTaskRepository().get(stale_session, task_id)
                assert stale_task is not None
                assert stale_task.status == TaskStatus.RUNNING.value
                with Session(app.state.engine) as competing_session:
                    paused = TaskCommandService().pause(competing_session, task_id)
                    competing_session.commit()
                    assert paused.status == TaskStatus.PAUSE_REQUESTED.value

                resumed = TaskCommandService().resume(stale_session, task_id)
                stale_session.commit()

                assert resumed.status == TaskStatus.RUNNING.value
                assert resumed.desired_state == "run"

            with Session(app.state.engine) as verify_session:
                final = verify_session.get(StudyTask, task_id)
                assert final is not None
                assert final.status == resumed.status
                assert final.desired_state == resumed.desired_state


def test_task_commands_require_csrf_and_reject_invalid_terminal_transition() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf, task_id = create_task(client)
            assert client.post(f"/api/v1/tasks/{task_id}/pause").status_code == 403
            with Session(app.state.engine) as session:
                task = session.get(StudyTask, task_id)
                assert task is not None
                task.status = "succeeded"
                session.commit()
            response = client.post(
                f"/api/v1/tasks/{task_id}/pause",
                headers={"X-CSRF-Token": csrf},
            )
            assert response.status_code == 409
            assert "terminal succeeded" in response.json()["detail"]
            missing = client.post(
                "/api/v1/tasks/missing/cancel",
                headers={"X-CSRF-Token": csrf},
            )
            assert missing.status_code == 404
