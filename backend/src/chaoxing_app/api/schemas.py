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


class CurrentUserResponse(BaseModel):
    username: str
    csrf_token: str


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
