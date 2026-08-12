import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import Event, StudyTask
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


def bootstrap_account(client: TestClient) -> tuple[str, int]:
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
            "speed": 1.5,
            "chapter_concurrency": 3,
            "unopened_policy": "skip",
            "user_agent": "Fixture Browser",
        },
    )
    assert account.status_code == 201
    return csrf_token, account.json()["id"]


def task_payload(account_id: int) -> dict[str, object]:
    return {
        "account_id": account_id,
        "course_id": "246831735",
        "class_id": "107515845",
        "cpi": "338350298",
        "course_title": "Fixture Course",
        "chapters": [
            {"chapter_id": "913820156", "title": "Chapter One", "position": 0},
            {"chapter_id": "913820157", "title": "Chapter Two", "position": 1},
        ],
    }


def test_task_creation_requires_csrf_and_persists_snapshot_and_event() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf_token, account_id = bootstrap_account(client)
            assert client.post("/api/v1/tasks", json=task_payload(account_id)).status_code == 403

            created = client.post(
                "/api/v1/tasks",
                headers={"X-CSRF-Token": csrf_token},
                json=task_payload(account_id),
            )
            assert created.status_code == 201
            body = created.json()
            assert body["status"] == "queued"
            assert body["account_label"] == "Primary"
            assert body["chapter_total"] == 2
            assert [chapter["chapter_id"] for chapter in body["chapters"]] == [
                "913820156",
                "913820157",
            ]
            assert "upstream-secret" not in created.text
            assert "cookie-secret" not in created.text

            with Session(app.state.engine) as db:
                task = db.scalar(select(StudyTask))
                assert task is not None
                assert task.config_snapshot == {
                    "speed": 1.5,
                    "chapter_concurrency": 3,
                    "unopened_policy": "skip",
                    "user_agent": "Fixture Browser",
                    "answer": {
                        "enabled": False,
                        "provider": "yanxi",
                        "submit_mode": "auto",
                        "threshold": 0.8,
                        "config_revision": 1,
                    },
                    "answer_enabled": False,
                    "answer_provider": "yanxi",
                    "answer_config_revision": 1,
                        "quiz_submission_mode": "auto",
                        "quiz_submit_threshold": 0.8,
                        "notifications": [
                            {
                                "channel": "server_chan",
                                "enabled": False,
                                "config_revision": 1,
                            },
                            {
                                "channel": "qmsg",
                                "enabled": False,
                                "config_revision": 1,
                            },
                            {
                                "channel": "bark",
                                "enabled": False,
                                "config_revision": 1,
                            },
                            {
                                "channel": "telegram",
                                "enabled": False,
                                "config_revision": 1,
                            },
                        ],
                    }
                event = db.scalar(select(Event))
                assert event is not None
                assert event.kind == "task.queued"
                serialized = json.dumps(event.payload)
                assert "upstream-secret" not in serialized
                assert "cookie-secret" not in serialized


def test_task_list_detail_duplicate_guard_and_event_replay() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf_token, account_id = bootstrap_account(client)
            first = client.post(
                "/api/v1/tasks",
                headers={"X-CSRF-Token": csrf_token},
                json=task_payload(account_id),
            )
            assert first.status_code == 201
            task_id = first.json()["id"]

            duplicate = client.post(
                "/api/v1/tasks",
                headers={"X-CSRF-Token": csrf_token},
                json=task_payload(account_id),
            )
            assert duplicate.status_code == 409

            listed = client.get("/api/v1/tasks")
            assert listed.status_code == 200
            assert [task["id"] for task in listed.json()] == [task_id]
            detail = client.get(f"/api/v1/tasks/{task_id}")
            assert detail.status_code == 200
            assert detail.json()["chapters"][0]["title"] == "Chapter One"

            events = client.get(f"/api/v1/tasks/{task_id}/events")
            assert events.status_code == 200
            assert [event["kind"] for event in events.json()] == ["task.queued"]

            replay = client.get(f"/api/v1/tasks/{task_id}/events/stream?follow=false")
            assert replay.status_code == 200
            assert replay.headers["content-type"].startswith("text/event-stream")
            assert "event: task.queued" in replay.text
            assert f'"task_id":"{task_id}"' in replay.text


def test_task_list_supports_stable_offset_pages() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf_token, account_id = bootstrap_account(client)
            headers = {"X-CSRF-Token": csrf_token}
            for index in range(3):
                payload = task_payload(account_id)
                payload.update(
                    {
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
                )
                response = client.post("/api/v1/tasks", headers=headers, json=payload)
                assert response.status_code == 201

            full = client.get("/api/v1/tasks", params={"limit": 200}).json()
            first_page = client.get(
                "/api/v1/tasks", params={"limit": 2, "offset": 0}
            ).json()
            second_page = client.get(
                "/api/v1/tasks", params={"limit": 2, "offset": 2}
            ).json()
            assert [task["id"] for task in first_page + second_page] == [
                task["id"] for task in full
            ]
            assert len(second_page) == 1


def test_task_input_rejects_duplicate_chapters_and_unknown_accounts() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf_token, account_id = bootstrap_account(client)
            unknown = task_payload(9999)
            response = client.post(
                "/api/v1/tasks",
                headers={"X-CSRF-Token": csrf_token},
                json=unknown,
            )
            assert response.status_code == 404

            duplicate_chapters = task_payload(account_id)
            duplicate_chapters["chapters"] = [
                {"chapter_id": "same", "title": "One", "position": 0},
                {"chapter_id": "same", "title": "Two", "position": 1},
            ]
            response = client.post(
                "/api/v1/tasks",
                headers={"X-CSRF-Token": csrf_token},
                json=duplicate_chapters,
            )
            assert response.status_code == 422


def test_task_snapshot_merges_account_answer_profile_and_rejects_incompatibility() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf_token, account_id = bootstrap_account(client)
            headers = {"X-CSRF-Token": csrf_token}
            configured = client.patch(
                "/api/v1/settings/integrations/answer",
                headers=headers,
                json={
                    "enabled": True,
                    "provider": "openai_compatible",
                    "base_url": "https://answers.example.test/v1",
                    "model": "global-model",
                    "api_key": "private-key",
                    "profile": {
                        "ensemble_enabled": False,
                        "models": [],
                        "referee_model": "",
                        "max_workers": 4,
                        "cache_enabled": True,
                        "cache_ttl_seconds": 604800,
                        "course_context_enabled": True,
                        "web_search_enabled": False,
                    },
                },
            )
            assert configured.status_code == 200
            overridden = client.patch(
                f"/api/v1/accounts/{account_id}",
                headers=headers,
                json={
                    "answer_profile_override": {
                        "cache_ttl_seconds": 3600,
                        "course_context_enabled": False,
                    }
                },
            )
            assert overridden.status_code == 200

            created = client.post(
                "/api/v1/tasks",
                headers=headers,
                json=task_payload(account_id),
            )
            assert created.status_code == 201
            with Session(app.state.engine) as db:
                task = db.scalar(select(StudyTask))
                assert task is not None
                answer = task.config_snapshot["answer"]
                assert answer["profile_source"] == "account"
                assert answer["profile"]["cache_ttl_seconds"] == 3600
                assert answer["profile"]["course_context_enabled"] is False
                assert answer["profile"]["cache_enabled"] is True
                assert "private-key" not in json.dumps(task.config_snapshot)

    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf_token, account_id = bootstrap_account(client)
            headers = {"X-CSRF-Token": csrf_token}
            configured = client.patch(
                "/api/v1/settings/integrations/answer",
                headers=headers,
                json={
                    "enabled": True,
                    "provider": "yanxi",
                    "endpoint": "https://answers.example.test/query",
                    "tokens": "private-token",
                },
            )
            assert configured.status_code == 200
            overridden = client.patch(
                f"/api/v1/accounts/{account_id}",
                headers=headers,
                json={"answer_profile_override": {"web_search_enabled": True}},
            )
            assert overridden.status_code == 200
            rejected = client.post(
                "/api/v1/tasks",
                headers=headers,
                json=task_payload(account_id),
            )
            assert rejected.status_code == 422
            assert rejected.json()["detail"] == (
                "account answer profile is incompatible with the selected provider"
            )
