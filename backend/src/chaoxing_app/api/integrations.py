from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from chaoxing_app.api.dependencies import (
    AuthContext,
    get_db,
    get_secret_box,
    require_admin,
    require_admin_csrf,
)
from chaoxing_app.api.integration_schemas import (
    AnswerIntegrationResponse,
    AnswerIntegrationUpdateRequest,
    AnswerProfileResponse,
    AnswerProviderPublicConfigResponse,
    IntegrationTestResponse,
    NotificationIntegrationResponse,
    NotificationIntegrationUpdateRequest,
)
from chaoxing_app.application.answer_runtime import build_answer_provider
from chaoxing_app.domain.integrations import NotificationChannelKind
from chaoxing_app.infrastructure.db.integrations import (
    AnswerIntegrationView,
    AnswerRuntimeConfiguration,
    IntegrationConfigurationError,
    IntegrationRevisionConflict,
    IntegrationSettingRepository,
    NotificationIntegrationView,
)
from chaoxing_app.infrastructure.notification_dispatcher import (
    NotificationSenderBuilder,
    build_notification_sender,
)
from chaoxing_app.infrastructure.notifications import (
    NotificationConfigurationError,
    NotificationError,
    NotificationHTTPError,
    NotificationMessage,
    NotificationTimeoutError,
)
from chaoxing_app.infrastructure.security.secrets import SecretBox
from chaoxing_app.platform.errors import (
    PlatformConfigurationError,
    PlatformError,
    PlatformHTTPError,
    PlatformTimeoutError,
)
from chaoxing_app.platform.task_points.quiz import (
    AnswerProvider,
    ProviderAnswer,
    QuizQuestion,
    QuizQuestionType,
)

router = APIRouter(prefix="/settings/integrations", tags=["integrations"])

_ANSWER_SECRET_FIELDS = ("tokens", "token", "api_key")
_NOTIFICATION_SECRET_FIELDS = ("webhook_url", "bot_token", "chat_id")
_TEST_NOTIFICATION = NotificationMessage(
    title="学习任务控制台测试通知",
    body="这是一条测试消息。收到说明该通知渠道配置正确。",
)
_TEST_QUESTION = QuizQuestion(
    question_id="integration-connectivity-test",
    title="中华人民共和国的首都是哪座城市?",
    question_type=QuizQuestionType.SINGLE,
    type_code="0",
    options=("A. 北京", "B. 上海", "C. 广州", "D. 深圳"),
)
_ANSWER_PREVIEW_LIMIT = 200

type AnswerProviderBuilder = Callable[
    [AnswerRuntimeConfiguration, requests.Session],
    AnswerProvider,
]


def get_notification_sender_builder() -> NotificationSenderBuilder:
    return build_notification_sender


def get_answer_provider_builder() -> AnswerProviderBuilder:
    return build_answer_provider


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _notification_failure_reason(error: Exception) -> str:
    if isinstance(error, NotificationConfigurationError):
        return "configuration_invalid"
    if isinstance(error, NotificationTimeoutError):
        return "timeout"
    if isinstance(error, NotificationHTTPError):
        return f"http_{error.status_code}"
    if isinstance(error, NotificationError):
        return "request_failed"
    return "sender_failure"


def _answer_failure_reason(error: Exception) -> str:
    if isinstance(error, PlatformConfigurationError):
        return "configuration_invalid"
    if isinstance(error, PlatformTimeoutError):
        return "timeout"
    if isinstance(error, PlatformHTTPError):
        return "provider_http_error"
    if isinstance(error, PlatformError):
        return "provider_failed"
    return "provider_failed"


def _answer_preview(value: ProviderAnswer | str | Sequence[str] | None) -> str | None:
    if value is None:
        return None
    raw = value.value if isinstance(value, ProviderAnswer) else value
    text = raw if isinstance(raw, str) else " / ".join(str(item) for item in raw)
    text = text.strip()
    return text[:_ANSWER_PREVIEW_LIMIT] if text else None


def _answer_response(view: AnswerIntegrationView) -> AnswerIntegrationResponse:
    return AnswerIntegrationResponse(
        enabled=view.enabled,
        provider=view.provider,
        submit_mode=view.submit_mode,
        threshold=view.threshold,
        revision=view.revision,
        config=AnswerProviderPublicConfigResponse.model_validate(view.config),
        profile=AnswerProfileResponse.model_validate(view.profile),
        has_tokens=view.has_tokens,
        has_token=view.has_token,
        has_api_key=view.has_api_key,
        updated_at=view.updated_at,
    )


def _notification_response(
    view: NotificationIntegrationView,
) -> NotificationIntegrationResponse:
    return NotificationIntegrationResponse(
        channel=view.channel,
        enabled=view.enabled,
        revision=view.revision,
        has_webhook_url=view.has_webhook_url,
        has_bot_token=view.has_bot_token,
        has_chat_id=view.has_chat_id,
        updated_at=view.updated_at,
    )


def _configuration_error(exc: IntegrationConfigurationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    )


def _revision_conflict(exc: IntegrationRevisionConflict) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get(
    "/answer",
    response_model=AnswerIntegrationResponse,
)
def get_answer_integration(
    _context: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
) -> AnswerIntegrationResponse:
    try:
        view = IntegrationSettingRepository(secret_box=secret_box).get_answer(db)
    except IntegrationConfigurationError as exc:
        raise _configuration_error(exc) from exc
    return _answer_response(view)


@router.patch(
    "/answer",
    response_model=AnswerIntegrationResponse,
)
@router.put(
    "/answer",
    response_model=AnswerIntegrationResponse,
)
def update_answer_integration(
    payload: AnswerIntegrationUpdateRequest,
    _context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
) -> AnswerIntegrationResponse:
    raw = payload.model_dump(exclude_unset=True, mode="json")
    expected_revision = raw.pop("expected_revision", None)
    reset = bool(raw.pop("reset", False))
    secret_changes: dict[str, object] = {}
    for field in _ANSWER_SECRET_FIELDS:
        clear = bool(raw.pop(f"clear_{field}", False))
        supplied = field in raw
        value = raw.pop(field, None)
        if clear and supplied:
            raise _configuration_error(
                IntegrationConfigurationError(
                    f"{field} cannot be set and cleared together"
                )
            )
        if clear:
            secret_changes[field] = None
        elif supplied:
            secret_changes[field] = value
    if reset and (raw or secret_changes):
        raise _configuration_error(
            IntegrationConfigurationError("reset cannot be combined with other answer settings")
        )
    try:
        view = IntegrationSettingRepository(secret_box=secret_box).update_answer(
            db,
            changes=raw,
            secret_changes=secret_changes,
            reset=reset,
            expected_revision=expected_revision,
        )
    except IntegrationRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    except IntegrationConfigurationError as exc:
        raise _configuration_error(exc) from exc
    return _answer_response(view)


@router.get("/notifications", response_model=list[NotificationIntegrationResponse])
def list_notification_integrations(
    _context: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
) -> list[NotificationIntegrationResponse]:
    try:
        views = IntegrationSettingRepository(secret_box=secret_box).list_notifications(db)
    except IntegrationConfigurationError as exc:
        raise _configuration_error(exc) from exc
    return [_notification_response(view) for view in views]


@router.get(
    "/notifications/{channel}",
    response_model=NotificationIntegrationResponse,
)
def get_notification_integration(
    channel: NotificationChannelKind,
    _context: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
) -> NotificationIntegrationResponse:
    try:
        view = IntegrationSettingRepository(secret_box=secret_box).get_notification(db, channel)
    except IntegrationConfigurationError as exc:
        raise _configuration_error(exc) from exc
    return _notification_response(view)


@router.patch(
    "/notifications/{channel}",
    response_model=NotificationIntegrationResponse,
)
@router.put(
    "/notifications/{channel}",
    response_model=NotificationIntegrationResponse,
)
def update_notification_integration(
    channel: NotificationChannelKind,
    payload: NotificationIntegrationUpdateRequest,
    _context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
) -> NotificationIntegrationResponse:
    raw = payload.model_dump(exclude_unset=True, mode="json")
    expected_revision = raw.pop("expected_revision", None)
    reset = bool(raw.pop("reset", False))
    enabled_value = raw.pop("enabled", None)
    enabled = enabled_value if isinstance(enabled_value, bool) else None
    secret_changes: dict[str, object] = {}
    for field in _NOTIFICATION_SECRET_FIELDS:
        clear = bool(raw.pop(f"clear_{field}", False))
        supplied = field in raw
        value = raw.pop(field, None)
        if clear and supplied:
            raise _configuration_error(
                IntegrationConfigurationError(
                    f"{field} cannot be set and cleared together"
                )
            )
        if clear:
            secret_changes[field] = None
        elif supplied:
            secret_changes[field] = value
    if reset and (enabled is not None or secret_changes):
        raise _configuration_error(
            IntegrationConfigurationError(
                "reset cannot be combined with other notification settings"
            )
        )
    try:
        view = IntegrationSettingRepository(secret_box=secret_box).update_notification(
            db,
            channel,
            enabled=enabled,
            secret_changes=secret_changes,
            reset=reset,
            expected_revision=expected_revision,
        )
    except IntegrationRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    except IntegrationConfigurationError as exc:
        raise _configuration_error(exc) from exc
    return _notification_response(view)


@router.post("/answer/test", response_model=IntegrationTestResponse)
def test_answer_integration(
    _context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
    provider_builder: AnswerProviderBuilder = Depends(get_answer_provider_builder),
) -> IntegrationTestResponse:
    repository = IntegrationSettingRepository(secret_box=secret_box)
    try:
        current = repository.get_answer(db)
        configuration = repository.answer_runtime_configuration(
            db,
            expected_revision=current.revision,
        )
    except IntegrationConfigurationError as exc:
        raise _configuration_error(exc) from exc
    if configuration is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请先启用并保存自动答题后再测试",
        )
    started = time.monotonic()
    session = requests.Session()
    try:
        provider = provider_builder(configuration, session)
        answer = _answer_preview(provider.answer(_TEST_QUESTION))
    except Exception as exc:
        return IntegrationTestResponse(
            ok=False,
            reason=_answer_failure_reason(exc),
            latency_ms=_elapsed_ms(started),
        )
    finally:
        session.close()
    return IntegrationTestResponse(
        ok=answer is not None,
        reason="" if answer is not None else "no_answer",
        answer=answer,
        latency_ms=_elapsed_ms(started),
    )


@router.post("/notifications/{channel}/test", response_model=IntegrationTestResponse)
def test_notification_integration(
    channel: NotificationChannelKind,
    _context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
    sender_builder: NotificationSenderBuilder = Depends(get_notification_sender_builder),
) -> IntegrationTestResponse:
    repository = IntegrationSettingRepository(secret_box=secret_box)
    try:
        current = repository.get_notification(db, channel)
        configuration = repository.notification_runtime_configuration(
            db,
            channel,
            expected_revision=current.revision,
        )
    except IntegrationConfigurationError as exc:
        raise _configuration_error(exc) from exc
    if configuration is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请先启用并保存该通知渠道后再测试",
        )
    started = time.monotonic()
    session = requests.Session()
    try:
        result = sender_builder(configuration, session).send(_TEST_NOTIFICATION)
    except Exception as exc:
        return IntegrationTestResponse(
            ok=False,
            reason=_notification_failure_reason(exc),
            latency_ms=_elapsed_ms(started),
        )
    finally:
        session.close()
    return IntegrationTestResponse(
        ok=result.accepted,
        reason="" if result.accepted else (result.reason or "provider_rejected"),
        latency_ms=_elapsed_ms(started),
    )
