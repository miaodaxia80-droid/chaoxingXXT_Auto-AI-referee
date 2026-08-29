from __future__ import annotations

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
    NotificationIntegrationResponse,
    NotificationIntegrationUpdateRequest,
)
from chaoxing_app.domain.integrations import NotificationChannelKind
from chaoxing_app.infrastructure.db.integrations import (
    AnswerIntegrationView,
    IntegrationConfigurationError,
    IntegrationRevisionConflict,
    IntegrationSettingRepository,
    NotificationIntegrationView,
)
from chaoxing_app.infrastructure.security.secrets import SecretBox

router = APIRouter(prefix="/settings/integrations", tags=["integrations"])

_ANSWER_SECRET_FIELDS = ("tokens", "token", "api_key")
_NOTIFICATION_SECRET_FIELDS = ("webhook_url", "bot_token", "chat_id")


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
