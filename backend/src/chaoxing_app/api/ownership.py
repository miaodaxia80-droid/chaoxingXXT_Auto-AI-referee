from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from chaoxing_app.api.dependencies import AuthContext
from chaoxing_app.infrastructure.db.models import Account, StudyTask


def scoped_user_id(context: AuthContext) -> int | None:
    """Owning app user for the current principal, or None for admins (unfiltered)."""
    return None if context.kind == "admin" else context.app_user.id


def owned_account_or_404(db: Session, account_id: int, context: AuthContext) -> Account:
    account = db.get(Account, account_id)
    if account is None or (
        context.kind == "app_user" and account.user_id != context.app_user.id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return account


def owned_task_or_404(db: Session, task_id: str, context: AuthContext) -> StudyTask:
    task = db.get(StudyTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    if context.kind == "app_user":
        account = db.get(Account, task.account_id)
        if account is None or account.user_id != context.app_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return task
