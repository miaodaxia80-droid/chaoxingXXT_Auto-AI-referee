from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import (
    AccountLease,
    Event,
    ManualInterventionResolution,
    StudyTask,
    TaskChapter,
    TaskRun,
)
from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings

PASSWORD = "correct-horse-battery-staple"


def make_client(temp_dir: str) -> tuple[object, TestClient]:
    data_dir = Path(temp_dir) / "data"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{(data_dir / 'app.db').as_posix()}",
    )
    app = create_app(settings)
    return app, TestClient(app)


def bootstrap(client: TestClient) -> tuple[str, int, str]:
    setup = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": PASSWORD},
    )
    assert setup.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    account = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "13800138000",
            "password": "upstream-secret",
            "remark": "Primary",
        },
    )
    assert account.status_code == 201
    task = client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": csrf},
        json={
            "account_id": account.json()["id"],
            "course_id": "course-1",
            "class_id": "class-1",
            "cpi": "cpi-1",
            "course_title": "Fixture Course",
            "chapters": [
                {"chapter_id": "chapter-1", "title": "Chapter One", "position": 0},
                {"chapter_id": "chapter-2", "title": "Chapter Two", "position": 1},
            ],
        },
    )
    assert task.status_code == 201
    return csrf, account.json()["id"], task.json()["id"]


def test_manual_intervention_resolution_is_audited_without_mutating_execution() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            assert client.get("/api/v1/operations/interventions").status_code == 401
            csrf, _account_id, task_id = bootstrap(client)
            with Session(app.state.engine) as db:  # type: ignore[attr-defined]
                chapters = list(
                    db.scalars(
                        select(TaskChapter)
                        .where(TaskChapter.task_id == task_id)
                        .order_by(TaskChapter.position)
                    )
                )
                chapters[0].status = "failed"
                chapters[0].last_error = "unsupported_task_point"
                chapters[0].finished_at = datetime.now(UTC) - timedelta(minutes=2)
                chapters[1].status = "unsubmitted"
                chapters[1].last_error = "quiz_requires_answers"
                chapters[1].finished_at = datetime.now(UTC) - timedelta(minutes=1)
                db.commit()
                first_id, second_id = chapters[0].id, chapters[1].id

            pending = client.get("/api/v1/operations/interventions")
            assert pending.status_code == 200
            assert [item["id"] for item in pending.json()] == [second_id, first_id]
            assert pending.json()[0]["account_label"] == "Primary"
            assert "upstream-secret" not in pending.text

            assert client.post(
                "/api/v1/operations/interventions/resolve",
                json={"ids": [first_id]},
            ).status_code == 403
            resolved = client.post(
                "/api/v1/operations/interventions/resolve",
                headers={"X-CSRF-Token": csrf},
                json={"ids": [first_id, second_id, first_id]},
            )
            assert resolved.status_code == 200
            assert resolved.json() == {
                "requested": 2,
                "resolved": 2,
                "already_resolved": 0,
                "not_actionable": 0,
            }
            assert client.get("/api/v1/operations/interventions").json() == []

            repeated = client.post(
                "/api/v1/operations/interventions/resolve",
                headers={"X-CSRF-Token": csrf},
                json={"ids": [first_id, 999_999]},
            )
            assert repeated.json() == {
                "requested": 2,
                "resolved": 0,
                "already_resolved": 1,
                "not_actionable": 1,
            }

            with Session(app.state.engine) as db:  # type: ignore[attr-defined]
                task = db.get(StudyTask, task_id)
                chapters = list(
                    db.scalars(select(TaskChapter).where(TaskChapter.task_id == task_id))
                )
                audit = list(db.scalars(select(ManualInterventionResolution)))
                events = list(
                    db.scalars(
                        select(Event).where(Event.kind == "operator.intervention_resolved")
                    )
                )
                assert task is not None
                assert {chapter.status for chapter in chapters} == {"failed", "unsubmitted"}
                assert len(audit) == 2
                assert all(row.resolved_by == "admin" for row in audit)
                assert len(events) == 2


def test_operations_health_reports_resources_and_durable_worker_state() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            assert client.get("/api/v1/operations/health").status_code == 401
            _csrf, account_id, task_id = bootstrap(client)
            now = datetime.now(UTC)
            with Session(app.state.engine) as db:  # type: ignore[attr-defined]
                task = db.get(StudyTask, task_id)
                assert task is not None
                task.status = "running"
                db.add(
                    TaskRun(
                        id="run-1",
                        task_id=task_id,
                        worker_id="worker-a",
                        process_id=1234,
                        fencing_token=1,
                        started_at=now - timedelta(minutes=2),
                        heartbeat_at=now - timedelta(minutes=1),
                    )
                )
                db.add(
                    AccountLease(
                        account_id=account_id,
                        task_id=task_id,
                        owner_id="worker-a",
                        fencing_token=1,
                        acquired_at=now - timedelta(minutes=2),
                        expires_at=now - timedelta(seconds=5),
                    )
                )
                db.commit()

            response = client.get("/api/v1/operations/health")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "degraded"
            assert body["uptime_seconds"] >= 0
            for name, unit in (
                ("cpu", "percent"),
                ("memory", "percent"),
                ("temperature", "celsius"),
            ):
                metric = body[name]
                assert metric["unit"] == unit
                assert metric["available"] is (metric["value"] is not None)
            worker = body["worker"]
            assert worker["enabled"] is True
            assert worker["service_running"] is False
            assert worker["configured_capacity"] == 2
            assert worker["active_process_count"] == 0
            assert worker["durable_active_run_count"] == 1
            assert worker["stale_run_count"] == 1
            assert worker["runs"][0]["worker_id"] == "worker-a"
            assert worker["runs"][0]["lease_stale"] is True
