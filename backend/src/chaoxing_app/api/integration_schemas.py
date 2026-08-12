from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from chaoxing_app.domain.integrations import (
    AnswerProviderKind,
    AnswerSubmitMode,
    NotificationChannelKind,
)


class AnswerProviderPublicConfigResponse(BaseModel):
    endpoint: str | None = None
    base_url: str | None = None
    model: str | None = None
    search: bool | None = None
    allow_unsafe_endpoint: bool = False


class AnswerProfileResponse(BaseModel):
    ensemble_enabled: bool
    models: list[str]
    referee_model: str
    max_workers: int
    cache_enabled: bool
    cache_ttl_seconds: int
    course_context_enabled: bool
    web_search_enabled: bool


class AnswerIntegrationResponse(BaseModel):
    enabled: bool
    provider: AnswerProviderKind
    submit_mode: AnswerSubmitMode
    threshold: float
    revision: int
    config: AnswerProviderPublicConfigResponse
    profile: AnswerProfileResponse | None = None
    has_tokens: bool
    has_token: bool
    has_api_key: bool
    updated_at: datetime


class AnswerIntegrationUpdateRequest(BaseModel):
    expected_revision: int | None = Field(default=None, strict=True, ge=1)
    enabled: bool | None = Field(default=None, strict=True)
    provider: AnswerProviderKind | None = None
    submit_mode: AnswerSubmitMode | None = None
    threshold: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    endpoint: str | None = Field(default=None, max_length=4_096)
    base_url: str | None = Field(default=None, max_length=4_096)
    model: str | None = Field(default=None, max_length=512)
    search: bool | None = Field(default=None, strict=True)
    allow_unsafe_endpoint: bool | None = Field(default=None, strict=True)
    profile: dict[str, object] | None = None

    # Secret fields intentionally have no schema validators. Validation occurs
    # after parsing so FastAPI never echoes secret input in a validation error.
    tokens: object | None = None
    token: object | None = None
    api_key: object | None = None
    clear_tokens: bool = Field(default=False, strict=True)
    clear_token: bool = Field(default=False, strict=True)
    clear_api_key: bool = Field(default=False, strict=True)
    reset: bool = Field(default=False, strict=True)


class NotificationIntegrationResponse(BaseModel):
    channel: NotificationChannelKind
    enabled: bool
    revision: int
    has_webhook_url: bool
    has_bot_token: bool
    has_chat_id: bool
    updated_at: datetime


class NotificationIntegrationUpdateRequest(BaseModel):
    expected_revision: int | None = Field(default=None, strict=True, ge=1)
    enabled: bool | None = Field(default=None, strict=True)
    webhook_url: object | None = None
    bot_token: object | None = None
    chat_id: object | None = None
    clear_webhook_url: bool = Field(default=False, strict=True)
    clear_bot_token: bool = Field(default=False, strict=True)
    clear_chat_id: bool = Field(default=False, strict=True)
    reset: bool = Field(default=False, strict=True)
