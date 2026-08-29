from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import ChapterStatus
from chaoxing_app.infrastructure.db.events import append_event
from chaoxing_app.infrastructure.db.models import (
    Account,
    ManualInterventionResolution,
    StudyTask,
    TaskChapter,
    utc_now,
)

_ATTENTION_STATUSES = (
    ChapterStatus.UNSUBMITTED.value,
    ChapterStatus.SKIPPED_NOT_OPEN.value,
    ChapterStatus.FAILED.value,
)


@dataclass(frozen=True, slots=True)
class ManualIntervention:
    id: int
    task_id: str
    account_id: int
    account_label: str
    course_title: str
    chapter_id: str
    chapter_title: str
    status: str
    reason: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    requested: int
    resolved: int
    already_resolved: int
    not_actionable: int


def list_manual_interventions(
    session: Session,
    *,
    limit: int = 200,
    user_id: int | None = None,
) -> list[ManualIntervention]:
    if not 1 <= limit <= 200:
        raise ValueError("manual intervention limit must be between 1 and 200")
    statement = (
        select(TaskChapter, StudyTask, Account)
        .join(StudyTask, StudyTask.id == TaskChapter.task_id)
        .join(Account, Account.id == StudyTask.account_id)
        .outerjoin(
            ManualInterventionResolution,
            (ManualInterventionResolution.task_id == TaskChapter.task_id)
            & (ManualInterventionResolution.chapter_id == TaskChapter.chapter_id),
        )
        .where(
            TaskChapter.status.in_(_ATTENTION_STATUSES),
            ManualInterventionResolution.id.is_(None),
        )
    )
    if user_id is not None:
        statement = statement.where(Account.user_id == user_id)
    rows = session.execute(
        statement.order_by(TaskChapter.finished_at.desc(), TaskChapter.id.desc()).limit(limit)
    ).all()
    return [
        ManualIntervention(
            id=chapter.id,
            task_id=task.id,
            account_id=account.id,
            account_label=account.remark or account.username_hint or f"Account {account.id}",
            course_title=task.course_title,
            chapter_id=chapter.chapter_id,
            chapter_title=chapter.chapter_title,
            status=chapter.status,
            reason=chapter.last_error,
            occurred_at=chapter.finished_at or task.updated_at,
        )
        for chapter, task, account in rows
    ]


def resolve_manual_interventions(
    session: Session,
    *,
    item_ids: tuple[int, ...],
    resolved_by: str,
    resolved_at: datetime | None = None,
    user_id: int | None = None,
) -> ResolutionResult:
    unique_ids = tuple(dict.fromkeys(item_ids))
    if not unique_ids:
        raise ValueError("at least one manual intervention id is required")
    if len(unique_ids) > 200:
        raise ValueError("at most 200 manual interventions can be resolved at once")

    row_statement = (
        select(TaskChapter, StudyTask)
        .join(StudyTask, StudyTask.id == TaskChapter.task_id)
        .where(TaskChapter.id.in_(unique_ids))
    )
    if user_id is not None:
        row_statement = row_statement.join(
            Account, Account.id == StudyTask.account_id
        ).where(Account.user_id == user_id)
    rows = {
        chapter.id: (chapter, task)
        for chapter, task in session.execute(row_statement).all()
    }
    task_ids = tuple({task.id for _chapter, task in rows.values()})
    existing = {
        (task_id, chapter_id)
        for task_id, chapter_id in session.execute(
            select(
                ManualInterventionResolution.task_id,
                ManualInterventionResolution.chapter_id,
            ).where(ManualInterventionResolution.task_id.in_(task_ids))
        )
    }
    current_time = resolved_at or utc_now()
    resolved = 0
    already_resolved = 0
    not_actionable = 0

    for item_id in unique_ids:
        row = rows.get(item_id)
        if row is None or row[0].status not in _ATTENTION_STATUSES:
            not_actionable += 1
            continue
        chapter, task = row
        if (task.id, chapter.chapter_id) in existing:
            already_resolved += 1
            continue
        resolution = ManualInterventionResolution(
            task_chapter_id=chapter.id,
            task_id=task.id,
            chapter_id=chapter.chapter_id,
            source_status=chapter.status,
            source_reason=chapter.last_error,
            resolved_by=resolved_by,
            resolved_at=current_time,
        )
        try:
            with session.begin_nested():
                session.add(resolution)
                session.flush()
        except IntegrityError:
            already_resolved += 1
            continue
        append_event(
            session,
            task_id=task.id,
            account_id=task.account_id,
            chapter_id=chapter.chapter_id,
            kind="operator.intervention_resolved",
            payload={"status": chapter.status},
        )
        resolved += 1

    return ResolutionResult(
        requested=len(unique_ids),
        resolved=resolved,
        already_resolved=already_resolved,
        not_actionable=not_actionable,
    )
