from __future__ import annotations

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
)
from chaoxing_app.infrastructure.db.models import Account, AppUser, StudyTask

router = APIRouter(prefix="/app-users", tags=["app-users"])


def _profile_response(user: AppUser) -> AppUserResponse:
    return AppUserResponse(
        id=user.id,
        nickname=user.nickname,
        avatar_url=user.avatar_url,
        quotas=dict(user.quotas),
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
        nickname=user.nickname,
        avatar_url=user.avatar_url,
        disabled=user.disabled,
        quotas=dict(user.quotas),
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
    if payload.nickname is not None:
        user.nickname = payload.nickname.strip()
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url.strip()
    if payload.quotas is not None:
        user.quotas = {**dict(user.quotas), **payload.quotas}
    return _admin_response(db, user)
