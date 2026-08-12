from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import IntegrationSetting, StudyTask
from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings


def make_client(temp_dir: str):
    data_dir = Path(temp_dir) / "data"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{(data_dir / 'integrations.db').as_posix()}",
    )
    app = create_app(settings)
    return app, TestClient(app)


def bootstrap_and_login(client: TestClient) -> str:
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


def create_account(client: TestClient, csrf: str) -> int:
    response = client.post(
        "/api/v1/accounts",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "13800138000",
            "password": "account-private-password",
            "remark": "Integration account",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_integration_reads_require_login_and_writes_require_csrf() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            answer_path = "/api/v1/settings/integrations/answer"
            notifications_path = "/api/v1/settings/integrations/notifications"
            assert client.get(answer_path).status_code == 401
            assert client.get(notifications_path).status_code == 401
            csrf = bootstrap_and_login(client)

            default = client.get(answer_path)
            assert default.status_code == 200
            assert default.json() == {
                "enabled": False,
                "provider": "yanxi",
                "submit_mode": "auto",
                "threshold": 0.8,
                "revision": 1,
                    "config": {
                    "endpoint": "https://tk.enncy.cn/query",
                    "base_url": None,
                    "model": None,
                    "search": None,
                    "allow_unsafe_endpoint": False,
                    },
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
                "has_tokens": False,
                "has_token": False,
                "has_api_key": False,
                "updated_at": default.json()["updated_at"],
            }
            assert client.patch(answer_path, json={"threshold": 0.5}).status_code == 403
            allowed = client.patch(
                answer_path,
                headers={"X-CSRF-Token": csrf},
                json={"threshold": 0.5},
            )
            assert allowed.status_code == 200
            stale = client.patch(
                answer_path,
                headers={"X-CSRF-Token": csrf},
                json={"expected_revision": 1, "threshold": 0.4},
            )
            assert stale.status_code == 409


@pytest.mark.parametrize(
    ("payload", "secret", "presence_field", "expected_config"),
    [
        (
            {
                "provider": "yanxi",
                "endpoint": "https://yanxi.example.test/query",
                "tokens": ["yanxi-private-token", "yanxi-second-private-token"],
            },
            "yanxi-private-token",
            "has_tokens",
            {"endpoint": "https://yanxi.example.test/query"},
        ),
        (
            {
                "provider": "like",
                "endpoint": "https://like.example.test/search",
                "model": "like-model",
                "search": True,
                "token": "like-private-token",
            },
            "like-private-token",
            "has_token",
            {
                "endpoint": "https://like.example.test/search",
                "model": "like-model",
                "search": True,
            },
        ),
        (
            {
                "provider": "tiku_adapter",
                "endpoint": "https://tiku.example.test/answer",
            },
            None,
            None,
            {"endpoint": "https://tiku.example.test/answer"},
        ),
        (
            {
                "provider": "openai_compatible",
                "base_url": "https://openai.example.test/v1",
                "model": "openai-model",
                "api_key": "openai-private-key",
            },
            "openai-private-key",
            "has_api_key",
            {"base_url": "https://openai.example.test/v1", "model": "openai-model"},
        ),
        (
            {
                "provider": "siliconflow",
                "base_url": "https://silicon.example.test/v1",
                "model": "silicon-model",
                "api_key": "silicon-private-key",
            },
            "silicon-private-key",
            "has_api_key",
            {"base_url": "https://silicon.example.test/v1", "model": "silicon-model"},
        ),
    ],
)
def test_each_answer_provider_validates_and_never_returns_secrets(
    payload: dict[str, object],
    secret: str | None,
    presence_field: str | None,
    expected_config: dict[str, object],
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf = bootstrap_and_login(client)
            response = client.patch(
                "/api/v1/settings/integrations/answer",
                headers={"X-CSRF-Token": csrf},
                json={
                    "enabled": True,
                    "submit_mode": "submit",
                    "threshold": 0.7,
                    **payload,
                },
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["enabled"] is True
            assert body["provider"] == payload["provider"]
            assert body["submit_mode"] == "submit"
            assert body["threshold"] == 0.7
            assert {key: body["config"][key] for key in expected_config} == expected_config
            if presence_field is not None:
                assert body[presence_field] is True
            if secret is not None:
                assert secret not in response.text
                with Session(app.state.engine) as session:
                    row = session.scalar(
                        select(IntegrationSetting).where(IntegrationSetting.kind == "answer")
                    )
                    assert row is not None
                    assert row.secret_config_encrypted is not None
                    assert secret not in row.secret_config_encrypted
                    assert secret not in json.dumps(row.public_config)


def test_answer_secret_omission_clear_and_reset_have_explicit_semantics() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf = bootstrap_and_login(client)
            path = "/api/v1/settings/integrations/answer"
            configured = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={
                    "enabled": True,
                    "provider": "yanxi",
                    "tokens": "retained-private-token",
                },
            )
            assert configured.status_code == 200
            revision = configured.json()["revision"]

            retained = client.put(
                path,
                headers={"X-CSRF-Token": csrf},
                json={"threshold": 0.6},
            )
            assert retained.status_code == 200
            assert retained.json()["has_tokens"] is True
            assert retained.json()["revision"] == revision + 1
            assert "retained-private-token" not in retained.text

            cannot_clear_enabled = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={"clear_tokens": True},
            )
            assert cannot_clear_enabled.status_code == 422
            assert "retained-private-token" not in cannot_clear_enabled.text

            cleared = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={"enabled": False, "clear_tokens": True},
            )
            assert cleared.status_code == 200
            assert cleared.json()["has_tokens"] is False

            reset = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={"reset": True},
            )
            assert reset.status_code == 200
            assert reset.json()["provider"] == "yanxi"
            assert reset.json()["threshold"] == 0.8
            assert reset.json()["enabled"] is False


def test_four_notification_channels_can_be_enabled_together_without_secret_exposure() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf = bootstrap_and_login(client)
            configurations = {
                "server_chan": {
                    "webhook_url": "https://notify.example.test/server-private-token"
                },
                "qmsg": {"webhook_url": "https://notify.example.test/qmsg-private-token"},
                "bark": {"webhook_url": "https://notify.example.test/bark-private-token"},
                "telegram": {
                    "bot_token": "123456:telegram-private-token",
                    "chat_id": "-100123456789",
                },
            }
            for channel, config in configurations.items():
                response = client.patch(
                    f"/api/v1/settings/integrations/notifications/{channel}",
                    headers={"X-CSRF-Token": csrf},
                    json={"enabled": True, **config},
                )
                assert response.status_code == 200, response.text
                assert response.json()["enabled"] is True
                assert all(value not in response.text for value in config.values())

            listed = client.get("/api/v1/settings/integrations/notifications")
            assert listed.status_code == 200
            assert [item["channel"] for item in listed.json()] == [
                "server_chan",
                "qmsg",
                "bark",
                "telegram",
            ]
            assert all(item["enabled"] for item in listed.json())
            serialized = listed.text
            for config in configurations.values():
                assert all(value not in serialized for value in config.values())

            with Session(app.state.engine) as session:
                rows = list(
                    session.scalars(
                        select(IntegrationSetting).where(
                            IntegrationSetting.kind.like("notification:%")
                        )
                    )
                )
                assert len(rows) == 4
                encrypted = "".join(row.secret_config_encrypted or "" for row in rows)
                for config in configurations.values():
                    assert all(value not in encrypted for value in config.values())


def test_notification_clear_requires_disable_and_reset_is_channel_local() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf = bootstrap_and_login(client)
            path = "/api/v1/settings/integrations/notifications/server_chan"
            created = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={
                    "enabled": True,
                    "webhook_url": "https://notify.example.test/private-token",
                },
            )
            assert created.status_code == 200

            retained = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={"enabled": False},
            )
            assert retained.status_code == 200
            assert retained.json()["has_webhook_url"] is True
            reenabled = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={"enabled": True},
            )
            assert reenabled.status_code == 200

            rejected = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={"clear_webhook_url": True},
            )
            assert rejected.status_code == 422
            cleared = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={"enabled": False, "clear_webhook_url": True},
            )
            assert cleared.status_code == 200
            assert cleared.json()["has_webhook_url"] is False


def test_validation_and_corruption_errors_do_not_echo_secret_input() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf = bootstrap_and_login(client)
            answer_path = "/api/v1/settings/integrations/answer"
            threshold_secret = "must-not-appear-in-threshold-error"
            invalid_threshold = client.patch(
                answer_path,
                headers={"X-CSRF-Token": csrf},
                json={
                    "provider": "openai_compatible",
                    "threshold": 2,
                    "api_key": threshold_secret,
                },
            )
            assert invalid_threshold.status_code == 422
            assert threshold_secret not in invalid_threshold.text

            url_secret = "private-webhook-token"
            invalid_url = client.patch(
                "/api/v1/settings/integrations/notifications/bark",
                headers={"X-CSRF-Token": csrf},
                json={"webhook_url": f"http://notify.invalid/{url_secret}"},
            )
            assert invalid_url.status_code == 422
            assert url_secret not in invalid_url.text

            configured = client.patch(
                answer_path,
                headers={"X-CSRF-Token": csrf},
                json={"provider": "yanxi", "tokens": "private-answer-token"},
            )
            assert configured.status_code == 200
            with Session(app.state.engine) as session:
                row = session.scalar(
                    select(IntegrationSetting).where(IntegrationSetting.kind == "answer")
                )
                assert row is not None
                row.secret_config_encrypted = "corrupted-private-ciphertext"
                session.commit()
            corrupted = client.get(answer_path)
            assert corrupted.status_code == 422
            assert "corrupted-private-ciphertext" not in corrupted.text
            assert "private-answer-token" not in corrupted.text

            malformed_answer_secret = "malformed-answer-token"
            malformed_answer = client.patch(
                answer_path,
                headers={"X-CSRF-Token": csrf},
                json={"tokens": [malformed_answer_secret, 123]},
            )
            assert malformed_answer.status_code == 422
            assert malformed_answer_secret not in malformed_answer.text

            malformed_notification_secret = "malformed-notification-token"
            malformed_notification = client.patch(
                "/api/v1/settings/integrations/notifications/bark",
                headers={"X-CSRF-Token": csrf},
                json={"webhook_url": {"token": malformed_notification_secret}},
            )
            assert malformed_notification.status_code == 422
            assert malformed_notification_secret not in malformed_notification.text


def test_task_creation_snapshots_answer_policy_and_revision_without_key() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf = bootstrap_and_login(client)
            api_key = "queue-must-never-contain-this-key"
            configured = client.patch(
                "/api/v1/settings/integrations/answer",
                headers={"X-CSRF-Token": csrf},
                json={
                    "enabled": True,
                    "provider": "siliconflow",
                    "submit_mode": "save_only",
                    "threshold": 0.55,
                    "api_key": api_key,
                },
            )
            assert configured.status_code == 200
            revision = configured.json()["revision"]
            account_id = create_account(client, csrf)
            created = client.post(
                "/api/v1/tasks",
                headers={"X-CSRF-Token": csrf},
                json={
                    "account_id": account_id,
                    "course_id": "course-1",
                    "class_id": "class-1",
                    "cpi": "cpi-1",
                    "course_title": "Integration course",
                    "chapters": [],
                },
            )
            assert created.status_code == 201

            with Session(app.state.engine) as session:
                task = session.scalar(select(StudyTask))
                assert task is not None
                assert task.config_snapshot["answer"] == {
                    "enabled": True,
                    "provider": "siliconflow",
                    "submit_mode": "save_only",
                    "threshold": 0.55,
                    "config_revision": revision,
                }
                serialized = json.dumps(task.config_snapshot)
                assert api_key not in serialized
                assert "api.siliconflow.cn" not in serialized


def test_local_tiku_adapter_requires_explicit_unsafe_endpoint_opt_in() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _app, client = make_client(temp_dir)
        with client:
            csrf = bootstrap_and_login(client)
            path = "/api/v1/settings/integrations/answer"
            rejected = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={
                    "provider": "tiku_adapter",
                    "endpoint": "http://192.168.1.20:8080/query",
                },
            )
            accepted = client.patch(
                path,
                headers={"X-CSRF-Token": csrf},
                json={
                    "provider": "tiku_adapter",
                    "endpoint": "http://192.168.1.20:8080/query",
                    "allow_unsafe_endpoint": True,
                    "enabled": True,
                },
            )

        assert rejected.status_code == 422
        assert accepted.status_code == 200
        assert accepted.json()["enabled"] is True
        assert accepted.json()["config"]["allow_unsafe_endpoint"] is True
