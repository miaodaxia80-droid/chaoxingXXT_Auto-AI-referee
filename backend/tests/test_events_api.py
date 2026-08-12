import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.events import append_event
from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings


def make_client(temp_dir: str):
    data_dir = Path(temp_dir) / "data"
    database_path = data_dir / "app.db"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{database_path.as_posix()}",
    )
    app = create_app(settings)
    return app, TestClient(app)


def bootstrap_task(client: TestClient) -> tuple[int, str]:
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
    csrf_token = login.json()["csrf_token"]
    account = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "username": "13800138000",
            "password": "upstream-secret",
            "cookies": "_uid=123; token=cookie-secret",
            "remark": "Primary",
        },
    )
    assert account.status_code == 201
    account_id = account.json()["id"]
    task = client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "account_id": account_id,
            "course_id": "course-1",
            "class_id": "class-1",
            "cpi": "cpi-1",
            "course_title": "Fixture Course",
            "chapters": [
                {"chapter_id": "chapter-1", "title": "Chapter One", "position": 0}
            ],
        },
    )
    assert task.status_code == 201
    return account_id, task.json()["id"]


def test_global_events_require_auth_and_apply_filters_in_latest_first_order() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            assert client.get("/api/v1/events").status_code == 401
            account_id, task_id = bootstrap_task(client)

            with Session(app.state.engine) as db:
                info = append_event(
                    db,
                    task_id=task_id,
                    account_id=account_id,
                    chapter_id="chapter-1",
                    kind="chapter.started",
                    payload={"status": "running"},
                )
                warning = append_event(
                    db,
                    task_id=task_id,
                    account_id=account_id,
                    chapter_id="chapter-1",
                    kind="chapter.quiz_unsubmitted",
                    level="warning",
                    payload={"task_type": "quiz"},
                )
                error = append_event(
                    db,
                    task_id=task_id,
                    account_id=account_id,
                    chapter_id="chapter-1",
                    kind="chapter.failed",
                    level="error",
                    payload={
                        "status": "failed",
                        "reason": "platform_response_invalid",
                        "run_id": "run-1",
                        "count": 2,
                        "answered_count": 3,
                        "total_questions": 4,
                        "coverage": 0.75,
                        "provider_error_count": 1,
                        "provider_result_reason": "coverage_below_threshold",
                        "channel": "bark",
                        "password": "payload-password",
                        "cookies": "payload-cookie",
                        "nested": {"token": "nested-token"},
                        "worker_id": "private-worker",
                        "fencing_token": 42,
                        "outbox_id": "private-outbox",
                        "note": "unapproved-field",
                    },
                )
                db.commit()
                info_id = info.id
                warning_id = warning.id
                error_id = error.id

            latest = client.get("/api/v1/events", params={"limit": 2})
            assert latest.status_code == 200
            assert [event["id"] for event in latest.json()] == [error_id, warning_id]

            after = client.get("/api/v1/events", params={"after_id": info_id})
            assert [event["id"] for event in after.json()] == [error_id, warning_id]

            by_account = client.get("/api/v1/events", params={"account_id": account_id})
            assert by_account.status_code == 200
            assert all(event["account_id"] == account_id for event in by_account.json())
            assert client.get("/api/v1/events", params={"account_id": 999}).json() == []

            by_task_and_level = client.get(
                "/api/v1/events",
                params={"task_id": task_id, "level": "warning"},
            )
            assert [event["id"] for event in by_task_and_level.json()] == [warning_id]

            stream = client.get(
                "/api/v1/events/stream",
                headers={"Last-Event-ID": str(info_id)},
                params={"follow": "false"},
            )
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert stream.text.index(f"id: {warning_id}") < stream.text.index(
                f"id: {error_id}"
            )
            assert f"id: {info_id}" not in stream.text

            serialized = latest.text
            for secret in (
                "payload-password",
                "payload-cookie",
                "nested-token",
                "private-worker",
                "private-outbox",
                "unapproved-field",
            ):
                assert secret not in serialized
                assert secret not in stream.text
            assert latest.json()[0]["chapter_title"] == "Chapter One"
            assert latest.json()[0]["payload"] == {
                "answered_count": 3,
                "channel": "bark",
                "count": 2,
                "coverage": 0.75,
                "provider_error_count": 1,
                "provider_result_reason": "coverage_below_threshold",
                "reason": "platform_response_invalid",
                "run_id": "run-1",
                "status": "failed",
                "total_questions": 4,
            }
            assert all(event["chapter_title"] == "Chapter One" for event in latest.json())
            assert stream.text.count('"chapter_title":"Chapter One"') == 2

            assert client.post(
                "/api/v1/events/archive",
                json={"before": (datetime.now(UTC) + timedelta(seconds=1)).isoformat()},
            ).status_code == 403

            csrf = client.post(
                "/api/v1/auth/login",
                json={
                    "username": "admin",
                    "password": "correct-horse-battery-staple",
                },
            ).json()["csrf_token"]
            archive = client.post(
                "/api/v1/events/archive",
                headers={"X-CSRF-Token": csrf},
                json={"before": (datetime.now(UTC) + timedelta(seconds=1)).isoformat()},
            )
            assert archive.status_code == 200
            assert archive.json()["archived"] >= 3
            assert archive.json()["archived_total"] >= 3
            assert client.get("/api/v1/events").json() == []

            archived_stream = client.get(
                "/api/v1/events/stream",
                params={"follow": "false"},
            )
            assert archived_stream.status_code == 200
            assert archived_stream.text == ""


def test_global_event_filter_validation_is_bounded() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            bootstrap_task(client)
            invalid_queries = (
                {"after_id": -1},
                {"account_id": 0},
                {"task_id": "x" * 37},
                {"level": "debug"},
                {"limit": 0},
                {"limit": 201},
            )
            for params in invalid_queries:
                assert client.get("/api/v1/events", params=params).status_code == 422
