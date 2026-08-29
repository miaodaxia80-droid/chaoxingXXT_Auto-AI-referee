from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chaoxing_app.api.dependencies import (
    AuthContext,
    get_db,
    require_admin,
    require_admin_csrf,
    require_app_user,
    require_csrf,
)
from chaoxing_app.api.quotas import ACTIVE_TASK_STATUSES
from chaoxing_app.api.schemas import (
    AppUserAdminResponse,
    AppUserAdminUpdateRequest,
    AppUserProfileUpdateRequest,
    AppUserResponse,
    CreateAppUserRequest,
)
from chaoxing_app.infrastructure.db.models import (
    Account,
    AdminUser,
    AppUser,
    StudyTask,
    default_user_quotas,
    ensure_utc,
    utc_now,
)
from chaoxing_app.infrastructure.security.passwords import PasswordService

router = APIRouter(prefix="/app-users", tags=["app-users"])

password_service = PasswordService()


def _profile_response(user: AppUser) -> AppUserResponse:
    return AppUserResponse(
        id=user.id,
        nickname=user.nickname,
        avatar_url=user.avatar_url,
        quotas=dict(user.quotas),
        username=user.username,
        plan_expires_at=user.plan_expires_at,
        task_credits=user.task_credits,
    )


def _admin_response(
    db: Session,
    user: AppUser,
) -> AppUserAdminResponse:
    account_count = int(
        db.scalar(select(func.count(Account.id)).where(Account.user_id == user.id)) or 0
    )
    active_task_count = int(
        db.scalar(
            select(func.count(StudyTask.id)).where(
                StudyTask.account_id.in_(
                    select(Account.id).where(Account.user_id == user.id)
                ),
                StudyTask.status.in_(ACTIVE_TASK_STATUSES),
            )
        )
        or 0
    )
    return AppUserAdminResponse(
        id=user.id,
        openid=user.openid,
        username=user.username,
        has_password=bool(user.password_hash),
        nickname=user.nickname,
        avatar_url=user.avatar_url,
        disabled=user.disabled,
        quotas=dict(user.quotas),
        plan_expires_at=user.plan_expires_at,
        task_credits=user.task_credits,
        account_count=account_count,
        active_task_count=active_task_count,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("/me", response_model=AppUserResponse)
def get_my_profile(
    context: AuthContext = Depends(require_app_user),
) -> AppUserResponse:
    return _profile_response(context.app_user)


@router.patch("/me", response_model=AppUserResponse)
def update_my_profile(
    payload: AppUserProfileUpdateRequest,
    context: AuthContext = Depends(require_csrf),
) -> AppUserResponse:
    if context.kind != "app_user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin session cannot access app user resources",
        )
    user = context.app_user
    if payload.nickname is not None:
        user.nickname = payload.nickname.strip()
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url.strip()
    return _profile_response(user)


@router.get("", response_model=list[AppUserAdminResponse])
def list_app_users(
    _context: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AppUserAdminResponse]:
    users = list(db.scalars(select(AppUser).order_by(AppUser.id)))
    return [_admin_response(db, user) for user in users]


@router.post("", response_model=AppUserAdminResponse, status_code=status.HTTP_201_CREATED)
def create_app_user(
    payload: CreateAppUserRequest,
    _context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> AppUserAdminResponse:
    username = payload.username.strip()
    if db.scalar(select(AdminUser).where(AdminUser.username == username)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username already taken")
    if db.scalar(select(AppUser).where(AppUser.username == username)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username already taken")
    user = AppUser(
        # openid is only used for WeChat login; local users store a placeholder.
        openid=f"local:{uuid.uuid4().hex[:16]}",
        username=username,
        password_hash=password_service.hash(payload.password),
        nickname=payload.nickname.strip() or username,
        quotas={**default_user_quotas(), **(payload.quotas or {})},
    )
    db.add(user)
    db.flush()
    return _admin_response(db, user)


@router.patch("/{user_id}", response_model=AppUserAdminResponse)
def update_app_user(
    user_id: int,
    payload: AppUserAdminUpdateRequest,
    _context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> AppUserAdminResponse:
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="app user not found")
    if payload.disabled is not None:
        user.disabled = payload.disabled
        if payload.disabled:
            user.session_version += 1  # revoke all sessions immediately
    if payload.nickname is not None:
        user.nickname = payload.nickname.strip()
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url.strip()
    if payload.quotas is not None:
        user.quotas = {**dict(user.quotas), **payload.quotas}
    if payload.password is not None:
        user.password_hash = password_service.hash(payload.password)
        user.session_version += 1  # kick old sessions after password reset
    if payload.plan_extend_days is not None and payload.plan_extend_days > 0:
        # extend from the later of now / current expiry
        base = max(ensure_utc(user.plan_expires_at) or utc_now(), utc_now())
        user.plan_expires_at = base + timedelta(days=payload.plan_extend_days)
    if payload.task_credits_add is not None:
        user.task_credits = max(0, user.task_credits + payload.task_credits_add)
    return _admin_response(db, user)
