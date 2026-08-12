from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Any, cast

from sqlalchemy import insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from chaoxing_app.domain.answer_profiles import (
    AnswerProfile,
    AnswerProfileError,
    parse_answer_profile,
    validate_answer_profile_provider,
)
from chaoxing_app.domain.integrations import (
    AnswerProviderKind,
    AnswerSubmitMode,
    NotificationChannelKind,
)
from chaoxing_app.infrastructure.db.models import IntegrationSetting
from chaoxing_app.infrastructure.notifications import (
    NotificationConfigurationError,
    validate_notification_webhook_url,
    validate_telegram_bot_token,
    validate_telegram_chat_id,
)
from chaoxing_app.infrastructure.security.secrets import SecretBox
from chaoxing_app.platform.answer_providers import (
    DEFAULT_LIKE_ENDPOINT,
    DEFAULT_SILICONFLOW_BASE_URL,
    DEFAULT_SILICONFLOW_MODEL,
    DEFAULT_YANXI_ENDPOINT,
)
from chaoxing_app.platform.answer_providers._common import validate_endpoint
from chaoxing_app.platform.errors import PlatformConfigurationError

ANSWER_INTEGRATION_KIND = "answer"
NOTIFICATION_KIND_PREFIX = "notification:"

_DEFAULT_PROVIDER_CONFIGS: dict[str, dict[str, object]] = {
    AnswerProviderKind.YANXI.value: {
        "endpoint": DEFAULT_YANXI_ENDPOINT,
        "allow_unsafe_endpoint": False,
    },
    AnswerProviderKind.LIKE.value: {
        "endpoint": DEFAULT_LIKE_ENDPOINT,
        "model": "",
        "search": False,
        "allow_unsafe_endpoint": False,
    },
    AnswerProviderKind.TIKU_ADAPTER.value: {
        "endpoint": "",
        "allow_unsafe_endpoint": False,
    },
    AnswerProviderKind.OPENAI_COMPATIBLE.value: {
        "base_url": "",
        "model": "",
        "allow_unsafe_endpoint": False,
    },
    AnswerProviderKind.SILICONFLOW.value: {
        "base_url": DEFAULT_SILICONFLOW_BASE_URL,
        "model": DEFAULT_SILICONFLOW_MODEL,
        "allow_unsafe_endpoint": False,
    },
}

_DEFAULT_ANSWER_PUBLIC: dict[str, object] = {
    "provider": AnswerProviderKind.YANXI.value,
    "submit_mode": AnswerSubmitMode.AUTO.value,
    "threshold": 0.8,
    "providers": _DEFAULT_PROVIDER_CONFIGS,
    "profile": AnswerProfile().to_json(),
}

_ANSWER_COMMON_FIELDS = frozenset(
    {"enabled", "provider", "submit_mode", "threshold", "profile"}
)
_PROVIDER_PUBLIC_FIELDS: dict[AnswerProviderKind, frozenset[str]] = {
    AnswerProviderKind.YANXI: frozenset({"endpoint", "allow_unsafe_endpoint"}),
    AnswerProviderKind.LIKE: frozenset(
        {"endpoint", "model", "search", "allow_unsafe_endpoint"}
    ),
    AnswerProviderKind.TIKU_ADAPTER: frozenset(
        {"endpoint", "allow_unsafe_endpoint"}
    ),
    AnswerProviderKind.OPENAI_COMPATIBLE: frozenset(
        {"base_url", "model", "allow_unsafe_endpoint"}
    ),
    AnswerProviderKind.SILICONFLOW: frozenset(
        {"base_url", "model", "allow_unsafe_endpoint"}
    ),
}
_PROVIDER_SECRET_FIELD: dict[AnswerProviderKind, str | None] = {
    AnswerProviderKind.YANXI: "tokens",
    AnswerProviderKind.LIKE: "token",
    AnswerProviderKind.TIKU_ADAPTER: None,
    AnswerProviderKind.OPENAI_COMPATIBLE: "api_key",
    AnswerProviderKind.SILICONFLOW: "api_key",
}


class IntegrationConfigurationError(ValueError):
    """A sanitized integration configuration failure suitable for an API response."""


class AnswerIntegrationRevisionMismatch(IntegrationConfigurationError):
    """The queued task points at an answer configuration that is no longer current."""


class IntegrationRevisionConflict(ValueError):
    """A settings write raced with another write against the same revision."""


class NotificationIntegrationRevisionMismatch(IntegrationConfigurationError):
    """The queued task points at a notification configuration that is no longer current."""




@dataclass(frozen=True, slots=True)
class AnswerIntegrationView:
    enabled: bool
    provider: AnswerProviderKind
    submit_mode: AnswerSubmitMode
    threshold: float
    revision: int
    config: dict[str, object]
    profile: dict[str, object]
    has_tokens: bool
    has_token: bool
    has_api_key: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerRuntimeConfiguration:
    provider: AnswerProviderKind
    revision: int
    endpoint: str = ""
    base_url: str = ""
    model: str = ""
    search: bool = False
    allow_unsafe_endpoint: bool = False
    credential: str | None = dataclass_field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class NotificationIntegrationView:
    channel: NotificationChannelKind
    enabled: bool
    revision: int
    has_webhook_url: bool
    has_bot_token: bool
    has_chat_id: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class NotificationRuntimeConfiguration:
    channel: NotificationChannelKind
    revision: int
    webhook_url: str | None = dataclass_field(default=None, repr=False)
    bot_token: str | None = dataclass_field(default=None, repr=False)
    chat_id: str | None = dataclass_field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class _AnswerState:
    provider: AnswerProviderKind
    submit_mode: AnswerSubmitMode
    threshold: float
    provider_configs: dict[str, dict[str, object]]
    profile: AnswerProfile

    def to_public_json(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "submit_mode": self.submit_mode.value,
            "threshold": self.threshold,
            "providers": deepcopy(self.provider_configs),
            "profile": self.profile.to_json(),
        }


class IntegrationSettingRepository:
    def __init__(self, *, secret_box: SecretBox) -> None:
        self._secret_box = secret_box

    def get_answer(self, session: Session) -> AnswerIntegrationView:
        model = self._get_or_create(session, ANSWER_INTEGRATION_KIND)
        state = self._read_answer_state(model.public_config)
        secrets = self._decrypt_secrets(model)
        return self._answer_view(model, state, secrets)

    def update_answer(
        self,
        session: Session,
        *,
        changes: dict[str, object],
        secret_changes: dict[str, object],
        reset: bool = False,
        expected_revision: int | None = None,
    ) -> AnswerIntegrationView:
        model = self._get_or_create(session, ANSWER_INTEGRATION_KIND)
        self._check_expected_revision(model.revision, expected_revision)
        old_state = self._read_answer_state(model.public_config)
        old_secrets = self._decrypt_secrets(model)

        if reset:
            if changes or secret_changes:
                raise IntegrationConfigurationError(
                    "reset cannot be combined with other answer settings"
                )
            state = self._read_answer_state(deepcopy(_DEFAULT_ANSWER_PUBLIC))
            enabled = False
            secrets: dict[str, str] = {}
        else:
            state, enabled = self._patch_answer_state(old_state, model.enabled, changes)
            secrets = self._patch_answer_secrets(state.provider, old_secrets, secret_changes)

        self._validate_answer_enabled(state, secrets, enabled=enabled)
        public_json = state.to_public_json()
        changed = (
            enabled != model.enabled
            or public_json != model.public_config
            or secrets != old_secrets
        )
        if changed:
            next_revision = model.revision + 1
            values: dict[str, object] = {
                "enabled": enabled,
                "public_config": public_json,
                "revision": next_revision,
            }
            if secrets != old_secrets:
                values["secret_config_encrypted"] = self._encrypt_secrets(model.kind, secrets)
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(IntegrationSetting)
                    .where(
                        IntegrationSetting.id == model.id,
                        IntegrationSetting.revision == model.revision,
                    )
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                raise IntegrationRevisionConflict("answer integration was changed concurrently")
            session.expire(model)
            session.refresh(model)
        return self._answer_view(model, state, secrets)

    def answer_task_snapshot(self, session: Session) -> dict[str, object]:
        """Return queue-safe policy metadata without decrypting provider secrets."""

        model = self._get_or_create(session, ANSWER_INTEGRATION_KIND)
        state = self._read_answer_state(model.public_config)
        policy: dict[str, object] = {
            "enabled": model.enabled,
            "provider": state.provider.value,
            "submit_mode": state.submit_mode.value,
            "threshold": state.threshold,
            "config_revision": model.revision,
        }
        if state.profile != AnswerProfile():
            policy["profile"] = state.profile.to_json()
        notifications = self.notification_task_snapshot(session)
        return {
            "answer": policy,
            "answer_enabled": model.enabled,
            "answer_provider": state.provider.value,
            "answer_config_revision": model.revision,
            "quiz_submission_mode": state.submit_mode.value,
            "quiz_submit_threshold": state.threshold,
            "notifications": notifications,
        }

    def notification_task_snapshot(self, session: Session) -> list[dict[str, object]]:
        """Return notification policy metadata without decrypting channel secrets."""

        return [
            {
                "channel": channel.value,
                "enabled": model.enabled,
                "config_revision": model.revision,
            }
            for channel in NotificationChannelKind
            for model in (self._get_or_create(session, self._notification_kind(channel)),)
        ]

    def answer_runtime_configuration(
        self,
        session: Session,
        *,
        expected_revision: int,
    ) -> AnswerRuntimeConfiguration | None:
        """Load secrets only when the queued task still targets the current revision."""

        if isinstance(expected_revision, bool) or expected_revision < 1:
            return None
        model = self._get_or_create(session, ANSWER_INTEGRATION_KIND)
        if model.revision != expected_revision:
            raise AnswerIntegrationRevisionMismatch(
                "answer integration revision no longer matches the queued task"
            )
        if not model.enabled:
            return None

        state = self._read_answer_state(model.public_config)
        selected = self._normalize_provider_config(
            state.provider,
            state.provider_configs[state.provider.value],
        )
        secrets = self._decrypt_secrets(model)
        self._validate_answer_enabled(state, secrets, enabled=True)
        secret_field = _PROVIDER_SECRET_FIELD[state.provider]
        credential = (
            secrets.get(f"{state.provider.value}.{secret_field}")
            if secret_field is not None
            else None
        )
        return AnswerRuntimeConfiguration(
            provider=state.provider,
            revision=model.revision,
            endpoint=self._runtime_text(selected, "endpoint"),
            base_url=self._runtime_text(selected, "base_url"),
            model=self._runtime_text(selected, "model"),
            search=self._runtime_switch(selected, "search"),
            allow_unsafe_endpoint=self._runtime_switch(
                selected,
                "allow_unsafe_endpoint",
            ),
            credential=credential,
        )

    def list_notifications(self, session: Session) -> list[NotificationIntegrationView]:
        return [self.get_notification(session, channel) for channel in NotificationChannelKind]

    def get_notification(
        self,
        session: Session,
        channel: NotificationChannelKind,
    ) -> NotificationIntegrationView:
        model = self._get_or_create(session, self._notification_kind(channel))
        secrets = self._decrypt_secrets(model)
        return self._notification_view(model, channel, secrets)

    def update_notification(
        self,
        session: Session,
        channel: NotificationChannelKind,
        *,
        enabled: bool | None,
        secret_changes: dict[str, object],
        reset: bool = False,
        expected_revision: int | None = None,
    ) -> NotificationIntegrationView:
        model = self._get_or_create(session, self._notification_kind(channel))
        self._check_expected_revision(model.revision, expected_revision)
        old_secrets = self._decrypt_secrets(model)
        if reset and (enabled is not None or secret_changes):
            raise IntegrationConfigurationError(
                "reset cannot be combined with other notification settings"
            )
        next_enabled = False if reset else (enabled if enabled is not None else model.enabled)
        secrets = {} if reset else self._patch_notification_secrets(
            channel, old_secrets, secret_changes
        )
        self._validate_notification_enabled(channel, secrets, enabled=next_enabled)

        changed = next_enabled != model.enabled or secrets != old_secrets
        if changed:
            next_revision = model.revision + 1
            values: dict[str, object] = {
                "enabled": next_enabled,
                "revision": next_revision,
            }
            if secrets != old_secrets:
                values["secret_config_encrypted"] = self._encrypt_secrets(model.kind, secrets)
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(IntegrationSetting)
                    .where(
                        IntegrationSetting.id == model.id,
                        IntegrationSetting.revision == model.revision,
                    )
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                raise IntegrationRevisionConflict(
                    f"{channel.value} notification was changed concurrently"
                )
            session.expire(model)
            session.refresh(model)
        return self._notification_view(model, channel, secrets)

    def notification_runtime_configuration(
        self,
        session: Session,
        channel: NotificationChannelKind,
        *,
        expected_revision: int,
    ) -> NotificationRuntimeConfiguration | None:
        if isinstance(expected_revision, bool) or expected_revision < 1:
            return None
        model = self._get_or_create(session, self._notification_kind(channel))
        if model.revision != expected_revision:
            raise NotificationIntegrationRevisionMismatch(
                "notification integration revision no longer matches the queued task"
            )
        if not model.enabled:
            return None
        secrets = self._decrypt_secrets(model)
        self._validate_notification_enabled(channel, secrets, enabled=True)
        if channel is NotificationChannelKind.TELEGRAM:
            return NotificationRuntimeConfiguration(
                channel=channel,
                revision=model.revision,
                bot_token=secrets.get("bot_token"),
                chat_id=secrets.get("chat_id"),
            )
        return NotificationRuntimeConfiguration(
            channel=channel,
            revision=model.revision,
            webhook_url=secrets.get("webhook_url"),
        )

    def _get_or_create(self, session: Session, kind: str) -> IntegrationSetting:
        model = session.scalar(select(IntegrationSetting).where(IntegrationSetting.kind == kind))
        if model is not None:
            return model
        public_config = deepcopy(_DEFAULT_ANSWER_PUBLIC) if kind == ANSWER_INTEGRATION_KIND else {}
        session.execute(
            insert(IntegrationSetting)
            .values(
                kind=kind,
                enabled=False,
                public_config=public_config,
                secret_config_encrypted=None,
                revision=1,
            )
            .prefix_with("OR IGNORE")
        )
        session.flush()
        model = session.scalar(select(IntegrationSetting).where(IntegrationSetting.kind == kind))
        if model is None:  # pragma: no cover - defensive database invariant
            raise RuntimeError("integration setting could not be initialized")
        return model

    def _read_answer_state(self, public: object) -> _AnswerState:
        try:
            if not isinstance(public, dict):
                raise ValueError
            provider = AnswerProviderKind(public["provider"])
            submit_mode = AnswerSubmitMode(public["submit_mode"])
            threshold_value = public["threshold"]
            if isinstance(threshold_value, bool) or not isinstance(threshold_value, (float, int)):
                raise ValueError
            threshold = float(threshold_value)
            if not 0.0 <= threshold <= 1.0:
                raise ValueError
            raw_configs = public["providers"]
            if not isinstance(raw_configs, dict):
                raise ValueError
            configs: dict[str, dict[str, object]] = deepcopy(_DEFAULT_PROVIDER_CONFIGS)
            for provider_name, value in raw_configs.items():
                if provider_name not in configs or not isinstance(value, dict):
                    continue
                if not all(isinstance(key, str) for key in value):
                    raise ValueError
                configs[provider_name].update(value)
            raw_profile = public.get("profile", AnswerProfile().to_json())
            if not isinstance(raw_profile, dict):
                raise ValueError
            profile = parse_answer_profile(raw_profile)
            return _AnswerState(provider, submit_mode, threshold, configs, profile)
        except (KeyError, TypeError, ValueError):
            raise IntegrationConfigurationError(
                "answer integration configuration is unavailable"
            ) from None

    def _patch_answer_state(
        self,
        state: _AnswerState,
        enabled: bool,
        changes: dict[str, object],
    ) -> tuple[_AnswerState, bool]:
        provider = state.provider
        if "provider" in changes:
            raw_provider = changes["provider"]
            if not isinstance(raw_provider, str):
                raise IntegrationConfigurationError("unsupported answer provider")
            try:
                provider = AnswerProviderKind(raw_provider)
            except ValueError:
                raise IntegrationConfigurationError("unsupported answer provider") from None

        allowed = _ANSWER_COMMON_FIELDS | _PROVIDER_PUBLIC_FIELDS[provider]
        unknown = set(changes) - allowed
        if unknown:
            raise IntegrationConfigurationError(
                f"fields are not valid for {provider.value} configuration"
            )

        next_enabled = enabled
        if "enabled" in changes:
            value = changes["enabled"]
            if not isinstance(value, bool):
                raise IntegrationConfigurationError("enabled must be a boolean")
            next_enabled = value

        submit_mode = state.submit_mode
        if "submit_mode" in changes:
            raw_submit_mode = changes["submit_mode"]
            if not isinstance(raw_submit_mode, str):
                raise IntegrationConfigurationError("unsupported answer submit mode")
            try:
                submit_mode = AnswerSubmitMode(raw_submit_mode)
            except ValueError:
                raise IntegrationConfigurationError("unsupported answer submit mode") from None

        threshold = state.threshold
        if "threshold" in changes:
            value = changes["threshold"]
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise IntegrationConfigurationError("answer threshold must be between 0 and 1")
            threshold = float(value)
            if not 0.0 <= threshold <= 1.0:
                raise IntegrationConfigurationError("answer threshold must be between 0 and 1")

        configs = deepcopy(state.provider_configs)
        selected = configs[provider.value]
        for key in _PROVIDER_PUBLIC_FIELDS[provider]:
            if key not in changes:
                continue
            value = changes[key]
            if key in {"search", "allow_unsafe_endpoint"}:
                if not isinstance(value, bool):
                    raise IntegrationConfigurationError(
                        "answer provider switches must be boolean"
                    )
                selected[key] = value
            elif not isinstance(value, str):
                raise IntegrationConfigurationError("answer provider text fields must be strings")
            else:
                selected[key] = value.strip()
        configs[provider.value] = self._normalize_provider_config(provider, selected)
        profile = state.profile
        if "profile" in changes:
            raw_profile = changes["profile"]
            if not isinstance(raw_profile, dict):
                raise IntegrationConfigurationError("answer profile is invalid")
            try:
                profile = parse_answer_profile(raw_profile)
            except ValueError as exc:
                raise IntegrationConfigurationError(str(exc)) from None
        return _AnswerState(provider, submit_mode, threshold, configs, profile), next_enabled

    def _normalize_provider_config(
        self,
        provider: AnswerProviderKind,
        config: dict[str, object],
    ) -> dict[str, object]:
        normalized: dict[str, object] = {}
        allow_unsafe = config.get("allow_unsafe_endpoint", False)
        if not isinstance(allow_unsafe, bool):
            raise IntegrationConfigurationError(
                "answer provider endpoint policy is invalid"
            )
        for key in _PROVIDER_PUBLIC_FIELDS[provider]:
            value = config.get(
                key,
                False if key in {"search", "allow_unsafe_endpoint"} else "",
            )
            if key in {"search", "allow_unsafe_endpoint"}:
                if not isinstance(value, bool):
                    raise IntegrationConfigurationError(
                        "answer provider switches must be boolean"
                    )
                normalized[key] = value
                continue
            if not isinstance(value, str):
                raise IntegrationConfigurationError("answer provider configuration is invalid")
            text = value.strip()
            if key in {"endpoint", "base_url"} and text:
                try:
                    text = validate_endpoint(
                        text,
                        allow_unsafe_endpoint=allow_unsafe,
                    )
                except PlatformConfigurationError:
                    raise IntegrationConfigurationError(
                        "answer provider endpoint is invalid"
                    ) from None
            normalized[key] = text
        return normalized

    def _patch_answer_secrets(
        self,
        provider: AnswerProviderKind,
        current: dict[str, str],
        changes: dict[str, object],
    ) -> dict[str, str]:
        expected = _PROVIDER_SECRET_FIELD[provider]
        if set(changes) - ({expected} if expected is not None else set()):
            raise IntegrationConfigurationError(
                f"secret fields are not valid for {provider.value} configuration"
            )
        secrets = dict(current)
        if expected is None or expected not in changes:
            return secrets
        storage_key = f"{provider.value}.{expected}"
        value = changes[expected]
        if value is None:
            secrets.pop(storage_key, None)
            return secrets
        if expected == "tokens":
            if isinstance(value, str):
                parts = value.split(",")
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                parts = value
            else:
                raise IntegrationConfigurationError("answer provider secret is invalid")
            if sum(len(part) for part in parts) > 32_768:
                raise IntegrationConfigurationError("answer provider secret is too long")
            normalized = ",".join(part.strip() for part in parts if part.strip())
        else:
            if not isinstance(value, str):
                raise IntegrationConfigurationError("answer provider secret is invalid")
            if len(value) > 8_192:
                raise IntegrationConfigurationError("answer provider secret is too long")
            normalized = value.strip()
        if not normalized:
            raise IntegrationConfigurationError("answer provider secret must not be empty")
        secrets[storage_key] = normalized
        return secrets

    def _validate_answer_enabled(
        self,
        state: _AnswerState,
        secrets: dict[str, str],
        *,
        enabled: bool,
    ) -> None:
        if not enabled:
            return
        try:
            validate_answer_profile_provider(state.profile, state.provider)
        except AnswerProfileError as exc:
            raise IntegrationConfigurationError(str(exc)) from None
        config = state.provider_configs[state.provider.value]
        required_public = (
            ("endpoint",)
            if state.provider in {
                AnswerProviderKind.YANXI,
                AnswerProviderKind.LIKE,
                AnswerProviderKind.TIKU_ADAPTER,
            }
            else ("base_url", "model")
        )
        if any(
            not isinstance(config.get(field), str) or not config[field]
            for field in required_public
        ):
            raise IntegrationConfigurationError(
                f"{state.provider.value} configuration is incomplete"
            )
        secret_field = _PROVIDER_SECRET_FIELD[state.provider]
        if secret_field is not None and not secrets.get(f"{state.provider.value}.{secret_field}"):
            raise IntegrationConfigurationError(
                f"{state.provider.value} configuration is incomplete"
            )

    def _patch_notification_secrets(
        self,
        channel: NotificationChannelKind,
        current: dict[str, str],
        changes: dict[str, object],
    ) -> dict[str, str]:
        allowed = {"bot_token", "chat_id"} if channel is NotificationChannelKind.TELEGRAM else {
            "webhook_url"
        }
        if set(changes) - allowed:
            raise IntegrationConfigurationError(
                f"secret fields are not valid for {channel.value} configuration"
            )
        secrets = dict(current)
        for field, value in changes.items():
            if value is None:
                secrets.pop(field, None)
                continue
            if not isinstance(value, str):
                raise IntegrationConfigurationError(
                    "notification secret configuration is invalid"
                )
            maximum_length = {
                "webhook_url": 32_768,
                "bot_token": 4_096,
                "chat_id": 1_024,
            }[field]
            if len(value) > maximum_length:
                raise IntegrationConfigurationError(
                    f"{channel.value} notification secret is too long"
                )
            try:
                if field == "webhook_url":
                    secrets[field] = validate_notification_webhook_url(value)
                elif field == "bot_token":
                    secrets[field] = validate_telegram_bot_token(value)
                else:
                    secrets[field] = validate_telegram_chat_id(value)
            except NotificationConfigurationError as exc:
                raise IntegrationConfigurationError(str(exc)) from None
        return secrets

    @staticmethod
    def _validate_notification_enabled(
        channel: NotificationChannelKind,
        secrets: dict[str, str],
        *,
        enabled: bool,
    ) -> None:
        if not enabled:
            return
        required = ("bot_token", "chat_id") if channel is NotificationChannelKind.TELEGRAM else (
            "webhook_url",
        )
        if any(not secrets.get(field) for field in required):
            raise IntegrationConfigurationError(
                f"{channel.value} notification configuration is incomplete"
            )

    def _decrypt_secrets(self, model: IntegrationSetting) -> dict[str, str]:
        if model.secret_config_encrypted is None:
            return {}
        try:
            plaintext = self._secret_box.decrypt(
                model.secret_config_encrypted,
                purpose=self._secret_purpose(model.kind),
            )
            decoded: object = json.loads(plaintext)
            if not isinstance(decoded, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in decoded.items()
            ):
                raise ValueError
            return dict(decoded)
        except Exception:
            raise IntegrationConfigurationError(
                "integration secret configuration is unavailable"
            ) from None

    def _encrypt_secrets(self, kind: str, secrets: dict[str, str]) -> str | None:
        if not secrets:
            return None
        plaintext = json.dumps(secrets, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return self._secret_box.encrypt(plaintext, purpose=self._secret_purpose(kind))

    @staticmethod
    def _secret_purpose(kind: str) -> str:
        return f"integration:{kind}:secrets"

    @staticmethod
    def _notification_kind(channel: NotificationChannelKind) -> str:
        return f"{NOTIFICATION_KIND_PREFIX}{channel.value}"

    @staticmethod
    def _check_expected_revision(current: int, expected: int | None) -> None:
        if expected is None:
            return
        if isinstance(expected, bool) or expected < 1 or expected != current:
            raise IntegrationRevisionConflict("integration revision is stale")

    @staticmethod
    def _runtime_text(config: dict[str, object], field_name: str) -> str:
        value = config.get(field_name, "")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _runtime_switch(config: dict[str, object], field_name: str) -> bool:
        value = config.get(field_name, False)
        return value if isinstance(value, bool) else False

    @staticmethod
    def _answer_view(
        model: IntegrationSetting,
        state: _AnswerState,
        secrets: dict[str, str],
    ) -> AnswerIntegrationView:
        has_tokens = (
            bool(secrets.get(f"{AnswerProviderKind.YANXI.value}.tokens"))
            if state.provider is AnswerProviderKind.YANXI
            else False
        )
        has_token = (
            bool(secrets.get(f"{AnswerProviderKind.LIKE.value}.token"))
            if state.provider is AnswerProviderKind.LIKE
            else False
        )
        if state.provider is AnswerProviderKind.OPENAI_COMPATIBLE:
            has_api_key = bool(
                secrets.get(f"{AnswerProviderKind.OPENAI_COMPATIBLE.value}.api_key")
            )
        elif state.provider is AnswerProviderKind.SILICONFLOW:
            has_api_key = bool(secrets.get(f"{AnswerProviderKind.SILICONFLOW.value}.api_key"))
        else:
            has_api_key = False
        return AnswerIntegrationView(
            enabled=model.enabled,
            provider=state.provider,
            submit_mode=state.submit_mode,
            threshold=state.threshold,
            revision=model.revision,
            config=deepcopy(state.provider_configs[state.provider.value]),
            profile=state.profile.to_json(),
            has_tokens=has_tokens,
            has_token=has_token,
            has_api_key=has_api_key,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _notification_view(
        model: IntegrationSetting,
        channel: NotificationChannelKind,
        secrets: dict[str, str],
    ) -> NotificationIntegrationView:
        return NotificationIntegrationView(
            channel=channel,
            enabled=model.enabled,
            revision=model.revision,
            has_webhook_url=bool(secrets.get("webhook_url")),
            has_bot_token=bool(secrets.get("bot_token")),
            has_chat_id=bool(secrets.get("chat_id")),
            updated_at=model.updated_at,
        )
