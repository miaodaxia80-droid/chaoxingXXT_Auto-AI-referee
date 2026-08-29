from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chaoxing_app.domain.answer_profiles import (
    AnswerProfileError,
    normalize_answer_profile_override,
)


def _validate_admin_password(value: str) -> str:
    if any(character < "!" or character > "~" for character in value):
        raise ValueError("password must contain only visible ASCII characters")
    return value


class SetupStatusResponse(BaseModel):
    required: bool


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("username must contain at least 3 characters")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_admin_password(value)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("username must not be empty")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_admin_password(value)


class AuthResponse(BaseModel):
    username: str
    csrf_token: str
    expires_at: str


class AppUserResponse(BaseModel):
    id: int
    nickname: str
    avatar_url: str
    quotas: dict[str, int]
    username: str | None = None
    plan_expires_at: datetime | None = None
    task_credits: int = 0


class WxLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    nickname: str | None = Field(default=None, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=2_048)


class DevLoginRequest(BaseModel):
    """Development-only login for browser previews (H5 build).

    The endpoint is gated behind CX_DEV_LOGIN_ENABLED and never available in production.
    """

    token: str = Field(min_length=1, max_length=64)
    nickname: str | None = Field(default=None, max_length=120)


class WxLoginResponse(BaseModel):
    csrf_token: str
    expires_at: str
    user: AppUserResponse


class CurrentUserResponse(BaseModel):
    username: str | None = None
    csrf_token: str
    kind: Literal["admin", "app_user"]
    user: AppUserResponse | None = None


_QUOTA_KEYS = {"max_accounts", "max_active_tasks"}


class AppUserAdminResponse(BaseModel):
    id: int
    openid: str
    username: str | None = None
    has_password: bool = False
    nickname: str
    avatar_url: str
    disabled: bool
    quotas: dict[str, int]
    plan_expires_at: datetime | None = None
    task_credits: int = 0
    account_count: int
    active_task_count: int
    created_at: datetime
    updated_at: datetime


class AppUserAdminUpdateRequest(BaseModel):
    disabled: bool | None = None
    nickname: str | None = Field(default=None, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=2_048)
    quotas: dict[str, int] | None = None
    # Password reset / entitlement adjustments: time cards extend from
    # max(now, current expiry); credits may go negative but floor at zero.
    password: str | None = Field(default=None, min_length=8, max_length=256)
    plan_extend_days: int | None = Field(default=None, ge=0, le=3_650)
    task_credits_add: int | None = Field(default=None, ge=-100_000, le=100_000)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str | None) -> str | None:
        return None if value is None else _validate_admin_password(value)

    @field_validator("quotas")
    @classmethod
    def validate_quotas(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        if set(value) - _QUOTA_KEYS:
            raise ValueError("unknown quota keys")
        if any(item < 0 or item > 10_000 for item in value.values()):
            raise ValueError("quota values must be between 0 and 10000")
        return value


class AppUserProfileUpdateRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=2_048)


# --- Local app users (username/password) and card keys ---


class CreateAppUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    nickname: str = Field(default="", max_length=120)
    quotas: dict[str, int] | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_admin_password(value)

    @field_validator("quotas")
    @classmethod
    def validate_quotas(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        if set(value) - _QUOTA_KEYS:
            raise ValueError("unknown quota keys")
        return value


class CardKeyIssueItem(BaseModel):
    code: str
    kind: str
    value: int


class CardKeyCreateRequest(BaseModel):
    kind: Literal["time", "count"]
    value: int
    count: int = Field(default=1, ge=1, le=500)
    batch: str = Field(default="", max_length=120)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value


class CardKeyResponse(BaseModel):
    id: int
    code_hint: str
    kind: str
    value: int
    batch: str
    status: str
    used_by: int | None = None
    used_at: datetime | None = None
    created_at: datetime


class CardKeyGenerateResponse(BaseModel):
    created: int
    items: list[CardKeyIssueItem]


class CardKeyRedeemRequest(BaseModel):
    code: str = Field(min_length=4, max_length=64)


class CardKeyRedeemResponse(BaseModel):
    kind: str
    value: int
    plan_expires_at: datetime | None
    task_credits: int


class EntitlementResponse(BaseModel):
    plan_expires_at: datetime | None
    task_credits: int
    active: bool


class AccountCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str | None = Field(default=None, max_length=512)
    cookies: str | None = Field(default=None, max_length=32_768)
    remark: str = Field(default="", max_length=120)
    user_agent: str = Field(default="", max_length=2_048)
    speed: float = Field(default=1.0, ge=1.0, le=2.0)
    chapter_concurrency: int = Field(default=1, ge=1, le=8)
    unopened_policy: str = "retry"
    answer_profile_override: dict[str, object] | None = None

    @field_validator("unopened_policy")
    @classmethod
    def validate_unopened_policy(cls, value: str) -> str:
        if value not in {"retry", "skip"}:
            raise ValueError("unopened_policy must be retry or skip")
        return value

    @field_validator("answer_profile_override")
    @classmethod
    def validate_answer_profile_override(
        cls, value: dict[str, object] | None
    ) -> dict[str, object] | None:
        try:
            return normalize_answer_profile_override(value)
        except AnswerProfileError as exc:
            raise ValueError(str(exc)) from None


class AccountUpdateRequest(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=256)
    password: str | None = Field(default=None, max_length=512)
    cookies: str | None = Field(default=None, max_length=32_768)
    remark: str | None = Field(default=None, max_length=120)
    user_agent: str | None = Field(default=None, max_length=2_048)
    speed: float | None = Field(default=None, ge=1.0, le=2.0)
    chapter_concurrency: int | None = Field(default=None, ge=1, le=8)
    unopened_policy: str | None = None
    enabled: bool | None = None
    clear_password: bool = False
    clear_cookies: bool = False
    answer_profile_override: dict[str, object] | None = None
    clear_answer_profile_override: bool = False

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("username must not be empty")
        return normalized

    @field_validator("unopened_policy")
    @classmethod
    def validate_optional_unopened_policy(cls, value: str | None) -> str | None:
        if value is not None and value not in {"retry", "skip"}:
            raise ValueError("unopened_policy must be retry or skip")
        return value

    @field_validator("answer_profile_override")
    @classmethod
    def validate_optional_answer_profile_override(
        cls, value: dict[str, object] | None
    ) -> dict[str, object] | None:
        try:
            return normalize_answer_profile_override(value)
        except AnswerProfileError as exc:
            raise ValueError(str(exc)) from None

    @model_validator(mode="after")
    def validate_secret_updates(self) -> Self:
        if self.clear_password and self.password:
            raise ValueError("password cannot be set and cleared together")
        if self.clear_cookies and self.cookies:
            raise ValueError("cookies cannot be set and cleared together")
        if self.clear_answer_profile_override and self.answer_profile_override:
            raise ValueError("answer profile cannot be set and cleared together")
        return self


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    remark: str
    username_hint: str
    enabled: bool
    user_agent: str
    speed: float
    chapter_concurrency: int
    unopened_policy: str
    has_password: bool
    has_cookies: bool
    answer_profile_override: dict[str, object] | None


class AccountImportRowResponse(BaseModel):
    line: int
    status: Literal["created", "duplicate", "invalid"]
    username_hint: str | None = None
    account_id: int | None = None
    error_code: str | None = None


class AccountImportResponse(BaseModel):
    total: int
    created: int
    duplicate: int
    invalid: int
    results: list[AccountImportRowResponse]
