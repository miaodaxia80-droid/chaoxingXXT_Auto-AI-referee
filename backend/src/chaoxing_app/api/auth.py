from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chaoxing_app.api.dependencies import (
    AuthContext,
    get_app_settings,
    get_db,
    get_login_dummy_password_hash,
    get_login_rate_limiter,
    get_wechat_client,
    require_auth,
    require_csrf,
)
from chaoxing_app.api.schemas import (
    AppUserResponse,
    AuthResponse,
    CurrentUserResponse,
    DevLoginRequest,
    LoginRequest,
    SetupRequest,
    SetupStatusResponse,
    WxLoginRequest,
    WxLoginResponse,
)
from chaoxing_app.infrastructure.db.models import AdminUser, AppUser, WebSession
from chaoxing_app.infrastructure.security.login_rate_limit import (
    LoginRateLimiter,
    RateLimitDecision,
)
from chaoxing_app.infrastructure.security.passwords import PasswordService
from chaoxing_app.infrastructure.security.tokens import (
    derive_csrf_token,
    digest_session_token,
    issue_session_token,
)
from chaoxing_app.infrastructure.wechat import (
    WeChatClient,
    WeChatClientError,
    WeChatConfigurationError,
    WeChatInvalidCodeError,
    WeChatRateLimitedError,
)
from chaoxing_app.settings import AppSettings

router = APIRouter(prefix="/auth", tags=["auth"])
password_service = PasswordService()

_WX_LOGIN_BUCKET = "wx-login"


def _app_user_response(user: AppUser) -> AppUserResponse:
    return AppUserResponse(
        id=user.id,
        nickname=user.nickname,
        avatar_url=user.avatar_url,
        quotas=dict(user.quotas),
    )


def _client_source(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _too_many_attempts(rate_limit: RateLimitDecision) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="too many login attempts",
        headers={"Retry-After": str(rate_limit.retry_after_seconds)},
    )


def _issue_session(
    db: Session,
    response: Response,
    settings: AppSettings,
    *,
    admin: AdminUser | None = None,
    app_user: AppUser | None = None,
) -> tuple[str, str]:
    """Persist a WebSession for one principal and set the session cookie.

    Returns (csrf_token, expires_at_iso).
    """
    if (admin is None) == (app_user is None):
        raise ValueError("exactly one principal must be provided")
    principal = admin if admin is not None else app_user
    assert principal is not None
    session_token = issue_session_token()
    csrf_plaintext = derive_csrf_token(session_token.plaintext)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
    db.add(
        WebSession(
            admin_id=admin.id if admin is not None else None,
            app_user_id=app_user.id if app_user is not None else None,
            token_digest=session_token.digest,
            csrf_digest=digest_session_token(csrf_plaintext),
            session_version=principal.session_version,
            expires_at=expires_at,
        )
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token.plaintext,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return csrf_plaintext, expires_at.isoformat()


@router.get("/setup", response_model=SetupStatusResponse)
def setup_status(db: Session = Depends(get_db)) -> SetupStatusResponse:
    return SetupStatusResponse(required=(db.scalar(select(func.count(AdminUser.id))) == 0))


@router.post("/setup", status_code=status.HTTP_201_CREATED)
def setup(payload: SetupRequest, db: Session = Depends(get_db)) -> dict[str, bool]:
    if db.scalar(select(func.count(AdminUser.id))) != 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="setup already completed")
    db.add(
        AdminUser(
            id=1,
            username=payload.username,
            password_hash=password_service.hash(payload.password),
        )
    )
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="setup already completed"
        ) from exc
    return {"ok": True}


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
    limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
    dummy_password_hash: str = Depends(get_login_dummy_password_hash),
) -> AuthResponse:
    source = _client_source(request)
    rate_limit = limiter.check(source, payload.username)
    if not rate_limit.allowed:
        raise _too_many_attempts(rate_limit)

    admin = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    candidate_hash = (
        admin.password_hash if admin is not None and not admin.disabled else dummy_password_hash
    )
    password_valid = password_service.verify(candidate_hash, payload.password)
    if admin is None or admin.disabled or not password_valid:
        rate_limit = limiter.record_failure(source, payload.username)
        if not rate_limit.allowed:
            raise _too_many_attempts(rate_limit)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    limiter.clear(source, payload.username)
    csrf_plaintext, expires_at = _issue_session(db, response, settings, admin=admin)
    return AuthResponse(
        username=admin.username,
        csrf_token=csrf_plaintext,
        expires_at=expires_at,
    )


@router.post("/wx/login", response_model=WxLoginResponse)
def wechat_login(
    payload: WxLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
    wechat_client: WeChatClient = Depends(get_wechat_client),
    limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
) -> WxLoginResponse:
    source = _client_source(request)
    rate_limit = limiter.check(source, _WX_LOGIN_BUCKET)
    if not rate_limit.allowed:
        raise _too_many_attempts(rate_limit)

    try:
        info = wechat_client.exchange_code(payload.code)
    except WeChatInvalidCodeError as exc:
        rate_limit = limiter.record_failure(source, _WX_LOGIN_BUCKET)
        if not rate_limit.allowed:
            raise _too_many_attempts(rate_limit) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid WeChat login code",
        ) from exc
    except WeChatRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="WeChat login is temporarily unavailable",
        ) from exc
    except WeChatConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WeChat login is not configured",
        ) from exc
    except WeChatClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="WeChat login failed",
        ) from exc

    user = db.scalar(select(AppUser).where(AppUser.openid == info.openid))
    if user is None:
        user = AppUser(openid=info.openid)
        db.add(user)
        db.flush()
    if payload.nickname is not None:
        user.nickname = payload.nickname
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url
    if user.disabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")

    limiter.clear(source, _WX_LOGIN_BUCKET)
    csrf_plaintext, expires_at = _issue_session(db, response, settings, app_user=user)
    return WxLoginResponse(
        csrf_token=csrf_plaintext,
        expires_at=expires_at,
        user=_app_user_response(user),
    )


@router.post("/dev-login", response_model=WxLoginResponse, include_in_schema=False)
def dev_login(
    payload: DevLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
    limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
) -> WxLoginResponse:
    """Development-only login for the H5 build preview.

    Gated behind CX_DEV_LOGIN_ENABLED and hard-refused in production; returns 404
    whenever it is not enabled so the endpoint never advertises itself.
    """
    if not settings.dev_login_enabled or settings.environment == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    source = _client_source(request)
    rate_limit = limiter.check(source, "dev-login")
    if not rate_limit.allowed:
        raise _too_many_attempts(rate_limit)

    openid = f"dev:{payload.token}"
    user = db.scalar(select(AppUser).where(AppUser.openid == openid))
    if user is None:
        user = AppUser(openid=openid)
        db.add(user)
        db.flush()
    if payload.nickname is not None:
        user.nickname = payload.nickname
    if user.disabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")

    limiter.clear(source, "dev-login")
    csrf_plaintext, expires_at = _issue_session(db, response, settings, app_user=user)
    return WxLoginResponse(
        csrf_token=csrf_plaintext,
        expires_at=expires_at,
        user=_app_user_response(user),
    )


@router.get("/me", response_model=CurrentUserResponse)
def current_user(
    response: Response,
    context: AuthContext = Depends(require_auth),
) -> CurrentUserResponse:
    csrf_token = derive_csrf_token(context.session_token)
    csrf_digest = digest_session_token(csrf_token)
    if not secrets.compare_digest(csrf_digest, context.web_session.csrf_digest):
        context.web_session.csrf_digest = csrf_digest
    response.headers["Cache-Control"] = "no-store"
    if context.kind == "admin":
        return CurrentUserResponse(
            username=context.admin.username,
            csrf_token=csrf_token,
            kind="admin",
        )
    return CurrentUserResponse(
        csrf_token=csrf_token,
        kind="app_user",
        user=_app_user_response(context.app_user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    context: AuthContext = Depends(require_csrf),
    settings: AppSettings = Depends(get_app_settings),
) -> None:
    context.web_session.revoked_at = datetime.now(UTC)
    response.delete_cookie(settings.session_cookie_name, path="/")
