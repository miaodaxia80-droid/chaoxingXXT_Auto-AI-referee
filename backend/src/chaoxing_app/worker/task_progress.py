from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Engine, and_, exists, insert, select, update
from sqlalchemy.engine import Connection, CursorResult

from chaoxing_app.domain.tasks import ChapterStatus, TaskStatus
from chaoxing_app.infrastructure.db.models import (
    Account,
    AccountLease,
    Event,
    StudyTask,
    TaskChapter,
    TaskRun,
    utc_now,
)
from chaoxing_app.infrastructure.db.task_queue import TaskClaim, is_task_lease_current
from chaoxing_app.worker.control import TaskLeaseLost

_LEASED_TASK_STATUSES = (
    TaskStatus.RUNNING.value,
    TaskStatus.PAUSE_REQUESTED.value,
    TaskStatus.CANCEL_REQUESTED.value,
)
_RUNNABLE_CHAPTER_STATUSES = (ChapterStatus.PENDING.value, ChapterStatus.RUNNING.value)
_SAFE_REASONS = frozenset(
    {
        "chapter_not_found",
        "chapter_not_open",
        "quiz_requires_answers",
        "unsupported_task_point",
        "unresolved_task_points",
        "platform_authentication_failed",
        "account_runtime_unavailable",
        "platform_response_invalid",
        "platform_completion_rejected",
        "platform_request_failed",
        "internal_execution_error",
    }
)


@dataclass(frozen=True, slots=True)
class TaskChapterSnapshot:
    chapter_id: str
    title: str
    position: int
    status: ChapterStatus


@dataclass(frozen=True, slots=True)
class TaskExecutionSnapshot:
    task_id: str
    account_id: int
    course_id: str
    class_id: str
    cpi: str
    course_title: str
    config: Mapping[str, object]
    chapters: tuple[TaskChapterSnapshot, ...]


def _claim_exists(claim: TaskClaim, checked_at: datetime) -> Any:
    conditions: list[Any] = [
        AccountLease.account_id == claim.account_id,
        AccountLease.task_id == claim.task_id,
        AccountLease.fencing_token == claim.fencing_token,
        AccountLease.expires_at > checked_at,
        Account.lease_version == claim.fencing_token,
        StudyTask.id == claim.task_id,
        StudyTask.account_id == claim.account_id,
        StudyTask.status.in_(_LEASED_TASK_STATUSES),
        TaskRun.id == claim.run_id,
        TaskRun.task_id == claim.task_id,
        TaskRun.fencing_token == claim.fencing_token,
        TaskRun.finished_at.is_(None),
    ]
    if claim.owner_id:
        conditions.extend(
            [AccountLease.owner_id == claim.owner_id, TaskRun.worker_id == claim.owner_id]
        )
    return exists(
        select(AccountLease.account_id)
        .select_from(AccountLease)
        .join(Account, Account.id == AccountLease.account_id)
        .join(
            StudyTask,
            and_(
                StudyTask.id == AccountLease.task_id,
                StudyTask.account_id == AccountLease.account_id,
            ),
        )
        .join(
            TaskRun,
            and_(
                TaskRun.id == claim.run_id,
                TaskRun.task_id == AccountLease.task_id,
                TaskRun.fencing_token == AccountLease.fencing_token,
            ),
        )
        .where(*conditions)
    )


def _rowcount(result: Any) -> int:
    return cast(CursorResult[Any], result).rowcount


class ClaimedTaskProgress:
    """Persists chapter progress only while a task claim still owns its lease."""

    def __init__(self, *, engine: Engine, claim: TaskClaim) -> None:
        self._engine = engine
        self._claim = claim

    def load(self) -> TaskExecutionSnapshot:
        self._require_current()
        with self._engine.connect() as connection:
            task = connection.execute(
                select(
                    StudyTask.id,
                    StudyTask.account_id,
                    StudyTask.course_id,
                    StudyTask.class_id,
                    StudyTask.cpi,
                    StudyTask.course_title,
                    StudyTask.config_snapshot,
                ).where(
                    StudyTask.id == self._claim.task_id,
                    StudyTask.account_id == self._claim.account_id,
                )
            ).one_or_none()
            if task is None:
                raise TaskLeaseLost
            rows = connection.execute(
                select(
                    TaskChapter.chapter_id,
                    TaskChapter.chapter_title,
                    TaskChapter.position,
                    TaskChapter.status,
                )
                .where(TaskChapter.task_id == self._claim.task_id)
                .order_by(TaskChapter.position, TaskChapter.id)
            ).all()
        self._require_current()
        return TaskExecutionSnapshot(
            task_id=task.id,
            account_id=task.account_id,
            course_id=task.course_id,
            class_id=task.class_id,
            cpi=task.cpi,
            course_title=task.course_title,
            config=dict(task.config_snapshot),
            chapters=tuple(
                TaskChapterSnapshot(
                    chapter_id=row.chapter_id,
                    title=row.chapter_title,
                    position=row.position,
                    status=ChapterStatus(row.status),
                )
                for row in rows
            ),
        )

    def start_chapter(self, chapter_id: str) -> None:
        occurred_at = utc_now()
        with self._engine.begin() as connection:
            result = connection.execute(
                update(TaskChapter)
                .where(
                    TaskChapter.task_id == self._claim.task_id,
                    TaskChapter.chapter_id == chapter_id,
                    TaskChapter.status.in_(_RUNNABLE_CHAPTER_STATUSES),
                    _claim_exists(self._claim, occurred_at),
                )
                .values(
                    status=ChapterStatus.RUNNING.value,
                    attempts=TaskChapter.attempts + 1,
                    last_error=None,
                    started_at=occurred_at,
                    finished_at=None,
                )
            )
            if _rowcount(result) != 1:
                raise TaskLeaseLost
            self._insert_event(
                connection,
                chapter_id=chapter_id,
                kind="chapter.started",
                level="info",
                payload={"status": ChapterStatus.RUNNING.value},
                occurred_at=occurred_at,
            )

    def finish_chapter(
        self,
        chapter_id: str,
        *,
        status: ChapterStatus,
        reason: str | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        if status in {ChapterStatus.PENDING, ChapterStatus.RUNNING}:
            raise ValueError("chapter completion status must be terminal")
        safe_reason = reason if reason in _SAFE_REASONS else None
        occurred_at = utc_now()
        with self._engine.begin() as connection:
            result = connection.execute(
                update(TaskChapter)
                .where(
                    TaskChapter.task_id == self._claim.task_id,
                    TaskChapter.chapter_id == chapter_id,
                    TaskChapter.status == ChapterStatus.RUNNING.value,
                    _claim_exists(self._claim, occurred_at),
                )
                .values(
                    status=status.value,
                    last_error=safe_reason,
                    finished_at=occurred_at,
                )
            )
            if _rowcount(result) != 1:
                raise TaskLeaseLost
            event_payload = {"status": status.value, **dict(payload or {})}
            if safe_reason:
                event_payload["reason"] = safe_reason
            self._insert_event(
                connection,
                chapter_id=chapter_id,
                kind=f"chapter.{status.value}",
                level=self._level(status),
                payload=event_payload,
                occurred_at=occurred_at,
            )

    def record_event(
        self,
        chapter_id: str,
        *,
        kind: str,
        payload: Mapping[str, object],
        level: str = "info",
    ) -> None:
        occurred_at = utc_now()
        with self._engine.begin() as connection:
            guard = connection.execute(
                update(TaskChapter)
                .where(
                    TaskChapter.task_id == self._claim.task_id,
                    TaskChapter.chapter_id == chapter_id,
                    TaskChapter.status == ChapterStatus.RUNNING.value,
                    _claim_exists(self._claim, occurred_at),
                )
                .values(status=TaskChapter.status)
            )
            if _rowcount(guard) != 1:
                raise TaskLeaseLost
            self._insert_event(
                connection,
                chapter_id=chapter_id,
                kind=kind,
                level=level,
                payload=payload,
                occurred_at=occurred_at,
            )

    def cancel_runnable_chapters(self) -> None:
        occurred_at = utc_now()
        with self._engine.begin() as connection:
            if not self._guard_claim(connection, occurred_at):
                raise TaskLeaseLost
            chapter_ids = tuple(
                connection.scalars(
                    select(TaskChapter.chapter_id).where(
                        TaskChapter.task_id == self._claim.task_id,
                        TaskChapter.status.in_(_RUNNABLE_CHAPTER_STATUSES),
                    )
                )
            )
            if not chapter_ids:
                return
            connection.execute(
                update(TaskChapter)
                .where(
                    TaskChapter.task_id == self._claim.task_id,
                    TaskChapter.chapter_id.in_(chapter_ids),
                    TaskChapter.status.in_(_RUNNABLE_CHAPTER_STATUSES),
                )
                .values(
                    status=ChapterStatus.CANCELED.value,
                    finished_at=occurred_at,
                    last_error=None,
                )
            )
            for chapter_id in chapter_ids:
                self._insert_event(
                    connection,
                    chapter_id=chapter_id,
                    kind="chapter.canceled",
                    level="info",
                    payload={"status": ChapterStatus.CANCELED.value},
                    occurred_at=occurred_at,
                )

    def statuses(self) -> tuple[ChapterStatus, ...]:
        self._require_current()
        with self._engine.connect() as connection:
            values = tuple(
                connection.scalars(
                    select(TaskChapter.status)
                    .where(TaskChapter.task_id == self._claim.task_id)
                    .order_by(TaskChapter.position, TaskChapter.id)
                )
            )
        self._require_current()
        return tuple(ChapterStatus(value) for value in values)

    def _guard_claim(self, connection: Connection, occurred_at: datetime) -> bool:
        result = connection.execute(
            update(TaskRun)
            .where(
                TaskRun.id == self._claim.run_id,
                TaskRun.task_id == self._claim.task_id,
                TaskRun.fencing_token == self._claim.fencing_token,
                TaskRun.finished_at.is_(None),
                _claim_exists(self._claim, occurred_at),
            )
            .values(heartbeat_at=TaskRun.heartbeat_at)
        )
        return _rowcount(result) == 1

    def _require_current(self) -> None:
        if not is_task_lease_current(self._engine, self._claim):
            raise TaskLeaseLost

    def _insert_event(
        self,
        connection: Connection,
        *,
        chapter_id: str,
        kind: str,
        level: str,
        payload: Mapping[str, object],
        occurred_at: datetime,
    ) -> None:
        connection.execute(
            insert(Event).values(
                task_id=self._claim.task_id,
                account_id=self._claim.account_id,
                chapter_id=chapter_id,
                kind=kind,
                level=level,
                payload={**dict(payload), "run_id": self._claim.run_id},
                occurred_at=occurred_at,
            )
        )

    @staticmethod
    def _level(status: ChapterStatus) -> str:
        if status is ChapterStatus.FAILED:
            return "error"
        if status in {ChapterStatus.UNSUBMITTED, ChapterStatus.SKIPPED_NOT_OPEN}:
            return "warning"
        return "info"
