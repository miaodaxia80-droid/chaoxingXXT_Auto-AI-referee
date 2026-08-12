from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Select, delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from chaoxing_app.domain.tasks import TERMINAL_TASK_STATUSES, TaskStatus
from chaoxing_app.infrastructure.db.events import append_event
from chaoxing_app.infrastructure.db.models import Account, StudyTask, TaskChapter


class DuplicateActiveTaskError(ValueError):
    pass


class TaskDeletionNotFoundError(ValueError):
    pass


class ActiveTaskDeletionError(ValueError):
    pass


_ACTIVE_TASK_UNIQUE_COLUMNS = (
    "study_tasks.account_id",
    "study_tasks.course_id",
    "study_tasks.class_id",
)


def _is_active_task_unique_violation(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return "unique constraint failed:" in message and all(
        column in message for column in _ACTIVE_TASK_UNIQUE_COLUMNS
    )


@dataclass(frozen=True, slots=True)
class NewTaskChapter:
    chapter_id: str
    title: str
    position: int


@dataclass(frozen=True, slots=True)
class NewStudyTask:
    account_id: int
    course_id: str
    class_id: str
    cpi: str
    course_title: str
    priority: int
    run_after: datetime
    chapters: tuple[NewTaskChapter, ...]
    config_snapshot: dict[str, Any]


def _with_task_details(statement: Select[tuple[StudyTask]]) -> Select[tuple[StudyTask]]:
    return statement.options(
        selectinload(StudyTask.account),
        selectinload(StudyTask.chapters),
    )


class StudyTaskRepository:
    def create(self, session: Session, new_task: NewStudyTask) -> StudyTask:
        active_statuses = [
            status.value for status in TaskStatus if status not in TERMINAL_TASK_STATUSES
        ]
        duplicate = session.scalar(
            select(StudyTask.id).where(
                StudyTask.account_id == new_task.account_id,
                StudyTask.course_id == new_task.course_id,
                StudyTask.class_id == new_task.class_id,
                StudyTask.status.in_(active_statuses),
            )
        )
        if duplicate is not None:
            raise DuplicateActiveTaskError("an active task already exists for this course")

        selected_ids = [chapter.chapter_id for chapter in new_task.chapters] or None
        task = StudyTask(
            account_id=new_task.account_id,
            course_id=new_task.course_id,
            class_id=new_task.class_id,
            cpi=new_task.cpi,
            course_title=new_task.course_title,
            priority=new_task.priority,
            run_after=new_task.run_after,
            selected_chapter_ids=selected_ids,
            config_snapshot=new_task.config_snapshot,
        )
        task.chapters = [
            TaskChapter(
                chapter_id=chapter.chapter_id,
                chapter_title=chapter.title,
                position=chapter.position,
            )
            for chapter in new_task.chapters
        ]
        try:
            # The read above gives callers an early, friendly error. The partial
            # unique index is the final guard when concurrent requests both pass it.
            with session.begin_nested():
                session.add(task)
                session.flush()
        except IntegrityError as exc:
            if _is_active_task_unique_violation(exc):
                raise DuplicateActiveTaskError(
                    "an active task already exists for this course"
                ) from exc
            raise
        append_event(
            session,
            task_id=task.id,
            account_id=task.account_id,
            kind="task.queued",
            payload={
                "course_id": task.course_id,
                "course_title": task.course_title,
                "selected_chapters": len(task.chapters),
            },
        )
        return task

    def get(self, session: Session, task_id: str) -> StudyTask | None:
        return session.scalar(_with_task_details(select(StudyTask).where(StudyTask.id == task_id)))

    def list(
        self,
        session: Session,
        *,
        account_id: int | None = None,
        status: TaskStatus | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[StudyTask]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must not be negative")
        statement = select(StudyTask)
        if account_id is not None:
            statement = statement.where(StudyTask.account_id == account_id)
        if status is not None:
            statement = statement.where(StudyTask.status == status.value)
        statement = (
            statement.order_by(StudyTask.created_at.desc(), StudyTask.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(session.scalars(_with_task_details(statement)))

    def delete_terminal(self, session: Session, task_id: str) -> None:
        current = session.scalar(select(StudyTask.status).where(StudyTask.id == task_id))
        if current is None:
            raise TaskDeletionNotFoundError("task not found")
        if TaskStatus(current) not in TERMINAL_TASK_STATUSES:
            raise ActiveTaskDeletionError("active tasks cannot be deleted")

        result = cast(
            CursorResult[Any],
            session.execute(
                delete(StudyTask).where(
                    StudyTask.id == task_id,
                    StudyTask.status.in_(
                        [status.value for status in TERMINAL_TASK_STATUSES]
                    ),
                )
            ),
        )
        if result.rowcount != 1:
            raise ActiveTaskDeletionError("task state changed; retry deletion")
        session.expire_all()

    def delete_terminal_history(self, session: Session) -> int:
        result = cast(
            CursorResult[Any],
            session.execute(
                delete(StudyTask).where(
                    StudyTask.status.in_(
                        [status.value for status in TERMINAL_TASK_STATUSES]
                    )
                )
            ),
        )
        session.expire_all()
        return int(result.rowcount or 0)


def public_account_snapshot(account: Account) -> dict[str, Any]:
    return {
        "speed": account.speed,
        "chapter_concurrency": account.chapter_concurrency,
        "unopened_policy": account.unopened_policy,
        "user_agent": account.user_agent,
    }
