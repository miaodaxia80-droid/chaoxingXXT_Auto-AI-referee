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
    require_auth,
    require_csrf,
)
from chaoxing_app.api.schemas import (
    AuthResponse,
    CurrentUserResponse,
    LoginRequest,
    SetupRequest,
    SetupStatusResponse,
)
from chaoxing_app.infrastructure.db.models import AdminUser, WebSession
from chaoxing_app.infrastructure.security.login_rate_limit import LoginRateLimiter
from chaoxing_app.infrastructure.security.passwords import PasswordService
from chaoxing_app.infrastructure.security.tokens import (
    derive_csrf_token,
    digest_session_token,
    issue_session_token,
)
from chaoxing_app.settings import AppSettings

router = APIRouter(prefix="/auth", tags=["auth"])
password_service = PasswordService()


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
    source = request.client.host if request.client is not None else "unknown"
    rate_limit = limiter.check(source, payload.username)
    if not rate_limit.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
            headers={"Retry-After": str(rate_limit.retry_after_seconds)},
        )

    admin = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    candidate_hash = (
        admin.password_hash if admin is not None and not admin.disabled else dummy_password_hash
    )
    password_valid = password_service.verify(candidate_hash, payload.password)
    if admin is None or admin.disabled or not password_valid:
        rate_limit = limiter.record_failure(source, payload.username)
        if not rate_limit.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many login attempts",
                headers={"Retry-After": str(rate_limit.retry_after_seconds)},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    limiter.clear(source, payload.username)
    session_token = issue_session_token()
    csrf_plaintext = derive_csrf_token(session_token.plaintext)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
    db.add(
        WebSession(
            admin_id=admin.id,
            token_digest=session_token.digest,
            csrf_digest=digest_session_token(csrf_plaintext),
            session_version=admin.session_version,
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
    return AuthResponse(
        username=admin.username,
        csrf_token=csrf_plaintext,
        expires_at=expires_at.isoformat(),
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
    return CurrentUserResponse(username=context.admin.username, csrf_token=csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    context: AuthContext = Depends(require_csrf),
    settings: AppSettings = Depends(get_app_settings),
) -> None:
    context.web_session.revoked_at = datetime.now(UTC)
    response.delete_cookie(settings.session_cookie_name, path="/")
