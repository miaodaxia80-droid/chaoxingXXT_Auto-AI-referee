from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chaoxing_app.domain.integrations import AnswerProviderKind, NotificationChannelKind
from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.integrations import (
    IntegrationConfigurationError,
    IntegrationRevisionConflict,
    IntegrationSettingRepository,
)
from chaoxing_app.infrastructure.db.models import IntegrationSetting
from chaoxing_app.infrastructure.security.secrets import SecretBox


def make_repository_database():
    temp_dir = tempfile.TemporaryDirectory()
    engine = create_database_engine(
        f"sqlite:///{(Path(temp_dir.name) / 'integrations.db').as_posix()}"
    )
    create_schema(engine)
    secret_box = SecretBox(b"i" * 32)
    return temp_dir, engine, secret_box, IntegrationSettingRepository(secret_box=secret_box)


def test_answer_secrets_are_one_encrypted_json_envelope_and_omission_retains_them() -> None:
    temp_dir, engine, secret_box, repository = make_repository_database()
    try:
        with Session(engine) as session:
            configured = repository.update_answer(
                session,
                changes={
                    "enabled": True,
                    "provider": "yanxi",
                    "submit_mode": "submit",
                    "threshold": 0.9,
                    "endpoint": "https://answers.example.test/query",
                },
                secret_changes={"tokens": "first-private-token,second-private-token"},
            )
            session.commit()
            assert configured.revision == 2
            assert configured.has_tokens is True

            row = session.scalar(
                select(IntegrationSetting).where(IntegrationSetting.kind == "answer")
            )
            assert row is not None
            assert row.secret_config_encrypted is not None
            assert "first-private-token" not in row.secret_config_encrypted
            assert "second-private-token" not in row.secret_config_encrypted
            assert "tokens" not in row.public_config
            plaintext = secret_box.decrypt(
                row.secret_config_encrypted,
                purpose="integration:answer:secrets",
            )
            assert json.loads(plaintext) == {
                "yanxi.tokens": "first-private-token,second-private-token"
            }
            ciphertext = row.secret_config_encrypted

            retained = repository.update_answer(
                session,
                changes={"threshold": 0.75},
                secret_changes={},
            )
            session.commit()
            assert retained.has_tokens is True
            assert retained.revision == 3
            session.refresh(row)
            assert row.secret_config_encrypted == ciphertext

            unchanged = repository.update_answer(
                session,
                changes={"threshold": 0.75},
                secret_changes={},
            )
            assert unchanged.revision == 3
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_provider_specific_secrets_survive_switches_without_being_reused() -> None:
    temp_dir, engine, _secret_box, repository = make_repository_database()
    try:
        with Session(engine) as session:
            openai = repository.update_answer(
                session,
                changes={
                    "enabled": True,
                    "provider": "openai_compatible",
                    "base_url": "https://openai.example.test/v1",
                    "model": "secure-model",
                },
                secret_changes={"api_key": "openai-private-key"},
            )
            assert openai.has_api_key is True

            like = repository.update_answer(
                session,
                changes={
                    "provider": "like",
                    "endpoint": "https://like.example.test/search",
                    "model": "like-model",
                    "search": True,
                },
                secret_changes={"token": "like-private-token"},
            )
            assert like.provider is AnswerProviderKind.LIKE
            assert like.has_token is True
            assert like.has_api_key is False

            restored = repository.update_answer(
                session,
                changes={"provider": "openai_compatible"},
                secret_changes={},
            )
            assert restored.provider is AnswerProviderKind.OPENAI_COMPATIBLE
            assert restored.has_api_key is True

            with pytest.raises(IntegrationConfigurationError):
                repository.update_answer(
                    session,
                    changes={"provider": "tiku_adapter", "enabled": True},
                    secret_changes={},
                )
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_notifications_are_independent_rows_and_reset_clears_only_target_channel() -> None:
    temp_dir, engine, secret_box, repository = make_repository_database()
    try:
        with Session(engine) as session:
            server_chan = repository.update_notification(
                session,
                NotificationChannelKind.SERVER_CHAN,
                enabled=True,
                secret_changes={
                    "webhook_url": "https://notify.example.test/server-private-token"
                },
            )
            telegram = repository.update_notification(
                session,
                NotificationChannelKind.TELEGRAM,
                enabled=True,
                secret_changes={
                    "bot_token": "123456:telegram-private-token",
                    "chat_id": "-100123456",
                },
            )
            session.commit()
            assert server_chan.enabled is True
            assert telegram.enabled is True
            assert telegram.has_bot_token is True
            assert telegram.has_chat_id is True
            assert session.scalar(select(func.count(IntegrationSetting.id))) == 2

            rows = list(
                session.scalars(
                    select(IntegrationSetting).order_by(IntegrationSetting.kind)
                )
            )
            serialized = "".join(row.secret_config_encrypted or "" for row in rows)
            assert "server-private-token" not in serialized
            assert "telegram-private-token" not in serialized
            telegram_row = next(row for row in rows if row.kind == "notification:telegram")
            assert telegram_row.secret_config_encrypted is not None
            decrypted = secret_box.decrypt(
                telegram_row.secret_config_encrypted,
                purpose="integration:notification:telegram:secrets",
            )
            assert json.loads(decrypted) == {
                "bot_token": "123456:telegram-private-token",
                "chat_id": "-100123456",
            }

            reset = repository.update_notification(
                session,
                NotificationChannelKind.TELEGRAM,
                enabled=None,
                secret_changes={},
                reset=True,
            )
            still_enabled = repository.get_notification(
                session, NotificationChannelKind.SERVER_CHAN
            )
            assert reset.enabled is False
            assert reset.has_bot_token is False
            assert reset.has_chat_id is False
            assert still_enabled.enabled is True
            assert still_enabled.has_webhook_url is True
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_enabled_integrations_require_complete_configuration() -> None:
    temp_dir, engine, _secret_box, repository = make_repository_database()
    try:
        with Session(engine) as session:
            with pytest.raises(IntegrationConfigurationError, match="incomplete"):
                repository.update_answer(
                    session,
                    changes={"enabled": True, "provider": "tiku_adapter"},
                    secret_changes={},
                )
            with pytest.raises(IntegrationConfigurationError, match="incomplete"):
                repository.update_notification(
                    session,
                    NotificationChannelKind.TELEGRAM,
                    enabled=True,
                    secret_changes={"bot_token": "123456:private-token"},
                )
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_enabled_answer_profiles_reject_unsupported_provider_combinations() -> None:
    temp_dir, engine, _secret_box, repository = make_repository_database()
    try:
        with Session(engine) as session:
            with pytest.raises(
                IntegrationConfigurationError,
                match="ensembles require a model-capable provider",
            ):
                repository.update_answer(
                    session,
                    changes={
                        "enabled": True,
                        "provider": "yanxi",
                        "endpoint": "https://answers.example.test/query",
                        "profile": {
                            "ensemble_enabled": True,
                            "models": ["model-a", "model-b"],
                            "referee_model": "referee",
                            "max_workers": 2,
                            "cache_enabled": True,
                            "cache_ttl_seconds": 3600,
                            "course_context_enabled": True,
                            "web_search_enabled": False,
                        },
                    },
                    secret_changes={"tokens": "private-token"},
                )

            with pytest.raises(
                IntegrationConfigurationError,
                match="ensemble models are required",
            ):
                repository.update_answer(
                    session,
                    changes={
                        "enabled": True,
                        "provider": "openai_compatible",
                        "base_url": "https://answers.example.test/v1",
                        "model": "model-a",
                        "profile": {
                            "ensemble_enabled": True,
                            "models": [],
                            "referee_model": "",
                            "max_workers": 2,
                            "cache_enabled": True,
                            "cache_ttl_seconds": 3600,
                            "course_context_enabled": True,
                            "web_search_enabled": False,
                        },
                    },
                    secret_changes={"api_key": "private-key"},
                )

            with pytest.raises(
                IntegrationConfigurationError,
                match="web search requires an OpenAI-compatible provider",
            ):
                repository.update_answer(
                    session,
                    changes={
                        "enabled": True,
                        "provider": "like",
                        "endpoint": "https://answers.example.test/query",
                        "profile": {
                            "ensemble_enabled": False,
                            "models": [],
                            "referee_model": "",
                            "max_workers": 2,
                            "cache_enabled": True,
                            "cache_ttl_seconds": 3600,
                            "course_context_enabled": True,
                            "web_search_enabled": True,
                        },
                    },
                    secret_changes={"token": "private-token"},
                )
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_integration_updates_support_expected_revision_compare_and_set() -> None:
    temp_dir, engine, _secret_box, repository = make_repository_database()
    try:
        with Session(engine) as session:
            current = repository.get_answer(session)
            updated = repository.update_answer(
                session,
                changes={"threshold": 0.7},
                secret_changes={},
                expected_revision=current.revision,
            )
            assert updated.revision == current.revision + 1
            with pytest.raises(IntegrationRevisionConflict):
                repository.update_answer(
                    session,
                    changes={"threshold": 0.6},
                    secret_changes={},
                    expected_revision=current.revision,
                )
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_task_snapshot_contains_policy_revision_but_no_secret_or_public_endpoint() -> None:
    temp_dir, engine, _secret_box, repository = make_repository_database()
    try:
        with Session(engine) as session:
            configured = repository.update_answer(
                session,
                changes={
                    "enabled": True,
                    "provider": "siliconflow",
                    "submit_mode": "save_only",
                    "threshold": 0.65,
                },
                secret_changes={"api_key": "silicon-private-key"},
            )
            snapshot = repository.answer_task_snapshot(session)

            assert snapshot["answer"] == {
                "enabled": True,
                "provider": "siliconflow",
                "submit_mode": "save_only",
                "threshold": 0.65,
                "config_revision": configured.revision,
            }
            serialized = json.dumps(snapshot)
            assert "silicon-private-key" not in serialized
            assert "api.siliconflow.cn" not in serialized
    finally:
        engine.dispose()
        temp_dir.cleanup()
