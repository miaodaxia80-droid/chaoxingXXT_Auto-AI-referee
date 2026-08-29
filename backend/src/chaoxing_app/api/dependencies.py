from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.infrastructure.db.models import AdminUser, AppUser, WebSession
from chaoxing_app.infrastructure.security.login_rate_limit import LoginRateLimiter
from chaoxing_app.infrastructure.security.secrets import SecretBox
from chaoxing_app.infrastructure.security.tokens import digest_session_token
from chaoxing_app.infrastructure.wechat import WeChatClient
from chaoxing_app.settings import AppSettings, normalize_http_origin


def get_app_settings(request: Request) -> AppSettings:
    return cast(AppSettings, request.app.state.settings)


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return cast(sessionmaker[Session], request.app.state.session_factory)


def get_secret_box(request: Request) -> SecretBox:
    return cast(SecretBox, request.app.state.secret_box)


def get_fingerprint_key(request: Request) -> bytes:
    return cast(bytes, request.app.state.master_key)


def get_login_rate_limiter(request: Request) -> LoginRateLimiter:
    return cast(LoginRateLimiter, request.app.state.login_rate_limiter)


def get_login_dummy_password_hash(request: Request) -> str:
    return cast(str, request.app.state.login_dummy_password_hash)


def get_wechat_client(request: Request) -> WeChatClient:
    return cast(WeChatClient, request.app.state.wechat_client)


def get_db(
    factory: sessionmaker[Session] = Depends(get_session_factory),
) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass(frozen=True, slots=True)
class AuthContext:
    principal: AdminUser | AppUser
    web_session: WebSession
    session_token: str

    @property
    def kind(self) -> Literal["admin", "app_user"]:
        return "admin" if isinstance(self.principal, AdminUser) else "app_user"

    @property
    def admin(self) -> AdminUser:
        if self.kind != "admin":
            raise AttributeError("authenticated principal is not an admin")
        return cast(AdminUser, self.principal)

    @property
    def app_user(self) -> AppUser:
        if self.kind != "app_user":
            raise AttributeError("authenticated principal is not an app user")
        return cast(AppUser, self.principal)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def require_auth(
    request: Request,
    settings: AppSettings = Depends(get_app_settings),
    db: Session = Depends(get_db),
) -> AuthContext:
    # Read the configured cookie name without exposing it as part of the API schema.
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if not cookie_value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    web_session = db.scalar(
        select(WebSession).where(WebSession.token_digest == digest_session_token(cookie_value))
    )
    now = datetime.now(UTC)
    if (
        web_session is None
        or web_session.revoked_at is not None
        or _as_utc(web_session.expires_at) <= now
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    if web_session.admin_id is not None:
        principal: AdminUser | AppUser | None = web_session.admin
    else:
        principal = web_session.app_user
    if (
        principal is None
        or principal.disabled
        or principal.session_version != web_session.session_version
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session revoked")
    return AuthContext(principal=principal, web_session=web_session, session_token=cookie_value)


def require_app_user(
    context: AuthContext = Depends(require_auth),
) -> AuthContext:
    """Restrict an endpoint to WeChat mini-program tenants (not admins)."""
    if context.kind != "app_user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin session cannot access app user resources",
        )
    return context


def require_admin(
    context: AuthContext = Depends(require_auth),
) -> AuthContext:
    """Restrict an endpoint to the operations administrator."""
    if context.kind != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin privileges required",
        )
    return context


def _require_trusted_origin(request: Request, settings: AppSettings) -> None:
    origin_header = request.headers.get("origin")
    if origin_header is None:
        return
    origin = normalize_http_origin(origin_header)
    configured = settings.allowed_origins
    trusted_values = configured or (f"{request.url.scheme}://{request.url.netloc}",)
    trusted_origins = {
        normalized
        for value in trusted_values
        if (normalized := normalize_http_origin(value)) is not None
    }
    if origin is None or origin not in trusted_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="untrusted request origin",
        )


def require_csrf(
    request: Request,
    context: AuthContext = Depends(require_auth),
    settings: AppSettings = Depends(get_app_settings),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> AuthContext:
    if not csrf_token or not secrets.compare_digest(
        digest_session_token(csrf_token), context.web_session.csrf_digest
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")
    _require_trusted_origin(request, settings)
    return context


def require_admin_csrf(
    context: AuthContext = Depends(require_csrf),
) -> AuthContext:
    """CSRF-protected admin-only endpoint."""
    if context.kind != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin privileges required",
        )
    return context
