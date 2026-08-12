from functools import lru_cache
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_http_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if scheme == "https" else 80
    authority = host if port is None or port == default_port else f"{host}:{port}"
    return f"{scheme}://{authority}"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    data_dir: Path = Path("data")
    frontend_dir: Path | None = None
    database_url: str = "sqlite:///data/chaoxing.db"
    api_prefix: str = "/api/v1"
    session_cookie_name: str = "cx_session"
    session_ttl_seconds: int = Field(default=86_400, ge=300)
    allowed_origins: tuple[str, ...] = ()
    login_rate_limit_max_failures: int = Field(default=5, ge=2, le=100)
    login_rate_limit_window_seconds: int = Field(default=300, ge=1, le=86_400)
    login_rate_limit_block_seconds: int = Field(default=900, ge=1, le=86_400)
    login_rate_limit_max_buckets: int = Field(default=10_000, ge=100, le=1_000_000)
    worker_max_workers: int = Field(default=2, ge=1, le=64)
    worker_poll_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        allow_inf_nan=False,
    )
    worker_lease_duration_seconds: float = Field(
        default=30.0,
        gt=0,
        allow_inf_nan=False,
    )
    worker_heartbeat_interval_seconds: float = Field(
        default=10.0,
        gt=0,
        allow_inf_nan=False,
    )
    worker_shutdown_grace_seconds: float = Field(
        default=20.0,
        ge=0,
        allow_inf_nan=False,
    )
    worker_max_recovery_attempts: int = Field(default=3, ge=0)
    worker_recovery_delay_seconds: float = Field(
        default=2.0,
        ge=0,
        allow_inf_nan=False,
    )

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            origin = normalize_http_origin(value)
            if origin is None:
                raise ValueError(f"invalid HTTP origin: {value!r}")
            if origin not in normalized:
                normalized.append(origin)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_worker_timing(self) -> Self:
        if self.worker_heartbeat_interval_seconds >= self.worker_lease_duration_seconds:
            raise ValueError("worker heartbeat interval must be shorter than lease duration")
        return self

    @property
    def secure_cookies(self) -> bool:
        return self.environment not in {"development", "test"}


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
