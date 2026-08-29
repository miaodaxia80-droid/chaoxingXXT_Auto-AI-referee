from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import TERMINAL_TASK_STATUSES, TaskStatus
from chaoxing_app.infrastructure.db.models import Account, AppUser, StudyTask, ensure_utc, utc_now

ACTIVE_TASK_STATUSES = [
    task_status.value for task_status in TaskStatus if task_status not in TERMINAL_TASK_STATUSES
]

DEFAULT_MAX_ACCOUNTS = 1
DEFAULT_MAX_ACTIVE_TASKS = 1


def _quota_value(user: AppUser, key: str, default: int) -> int:
    raw = (user.quotas or {}).get(key)
    if isinstance(raw, int) and raw >= 0:
        return raw
    return default


def _owned_account_count(db: Session, user: AppUser) -> int:
    return int(
        db.scalar(select(func.count(Account.id)).where(Account.user_id == user.id)) or 0
    )


def _owned_active_task_count(db: Session, user: AppUser) -> int:
    return int(
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


def ensure_account_quota(db: Session, user: AppUser) -> None:
    if _owned_account_count(db, user) >= _quota_value(user, "max_accounts", DEFAULT_MAX_ACCOUNTS):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="account quota exceeded",
        )


def ensure_task_quota(db: Session, user: AppUser) -> None:
    if _owned_active_task_count(db, user) >= _quota_value(
        user, "max_active_tasks", DEFAULT_MAX_ACTIVE_TASKS
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="task quota exceeded",
        )


def ensure_task_entitlement(user: AppUser) -> None:
    """Card-key entitlement gate: unlimited while a time card is active;
    otherwise consumes one count-card credit; rejects when neither exists.

    Credits are consumed at task creation (queuing occupies one execution
    slot; cancellation does not refund).
    """
    now = utc_now()
    expires = ensure_utc(user.plan_expires_at)
    if expires is not None and expires > now:
        return
    if user.task_credits > 0:
        user.task_credits -= 1
        return
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail="task entitlement exhausted; redeem a card key",
    )
