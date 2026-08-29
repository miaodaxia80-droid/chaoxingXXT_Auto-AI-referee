from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from chaoxing_app.api.dependencies import (
    AuthContext,
    get_db,
    get_secret_box,
    require_admin,
    require_admin_csrf,
    require_auth,
)
from chaoxing_app.domain.settings import normalize_clock_time, validate_timezone
from chaoxing_app.infrastructure.db.integrations import IntegrationSettingRepository
from chaoxing_app.infrastructure.db.models import SystemSettings
from chaoxing_app.infrastructure.db.system_settings import (
    SystemSettingsRepository,
    SystemSettingsUpdate,
)
from chaoxing_app.infrastructure.security.secrets import SecretBox

router = APIRouter(prefix="/settings", tags=["settings"])


class SystemSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    worker_enabled: bool
    run_window_enabled: bool
    run_window_start: str
    run_window_end: str
    timezone: str
    event_retention_days: int
    updated_at: datetime


class SystemSettingsUpdateRequest(BaseModel):
    worker_enabled: bool | None = None
    run_window_enabled: bool | None = None
    run_window_start: str | None = None
    run_window_end: str | None = None
    timezone: str | None = None
    event_retention_days: int | None = Field(default=None, strict=True, ge=1, le=3650)

    @field_validator("run_window_start", "run_window_end")
    @classmethod
    def validate_clock_time(cls, value: str | None) -> str | None:
        return normalize_clock_time(value) if value is not None else None

    @field_validator("timezone")
    @classmethod
    def validate_timezone_name(cls, value: str | None) -> str | None:
        return validate_timezone(value) if value is not None else None


def _to_response(settings: SystemSettings) -> SystemSettingsResponse:
    return SystemSettingsResponse.model_validate(settings)


@router.get("", response_model=SystemSettingsResponse)
def get_system_settings(
    _context: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SystemSettingsResponse:
    return _to_response(SystemSettingsRepository().get(db))


@router.patch("", response_model=SystemSettingsResponse)
@router.put("", response_model=SystemSettingsResponse, include_in_schema=False)
def update_system_settings(
    payload: SystemSettingsUpdateRequest,
    _context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> SystemSettingsResponse:
    try:
        settings = SystemSettingsRepository().update(
            db,
            SystemSettingsUpdate(**payload.model_dump()),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _to_response(settings)


class AnswerPublicResponse(BaseModel):
    """Sanitized platform answer configuration visible to app users.

    Secrets (tokens, API keys) are never included.
    """

    enabled: bool
    provider: str
    submit_mode: str
    threshold: float
    config: dict[str, object]
    profile: dict[str, object]


@router.get("/answer-public", response_model=AnswerPublicResponse)
def get_answer_public_settings(
    _context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
) -> AnswerPublicResponse:
    view = IntegrationSettingRepository(secret_box=secret_box).get_answer(db)
    return AnswerPublicResponse(
        enabled=view.enabled,
        provider=view.provider.value,
        submit_mode=view.submit_mode.value,
        threshold=view.threshold,
        config=view.config,
        profile=view.profile,
    )
