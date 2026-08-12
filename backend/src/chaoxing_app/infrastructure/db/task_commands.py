from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import (
    ChapterStatus,
    DesiredTaskState,
    TaskStatus,
    ensure_task_transition,
    is_terminal_task,
)
from chaoxing_app.infrastructure.db.events import append_event
from chaoxing_app.infrastructure.db.models import StudyTask, TaskChapter, utc_now
from chaoxing_app.infrastructure.db.tasks import StudyTaskRepository


class TaskCommandError(ValueError):
    pass


class TaskCommandNotFoundError(TaskCommandError):
    pass


class TaskCommandConflictError(TaskCommandError):
    pass


@dataclass(frozen=True, slots=True)
class _Transition:
    target: TaskStatus
    desired_state: DesiredTaskState
    event_kind: str
    terminal: bool = False


class TaskCommandService:
    def pause(
        self,
        session: Session,
        task_id: str,
        *,
        origin: str | None = None,
    ) -> StudyTask:
        if origin not in {None, "run_window"}:
            raise ValueError("invalid task pause origin")
        task, current = self._load(session, task_id)
        if current in {TaskStatus.PAUSED, TaskStatus.PAUSE_REQUESTED}:
            return task
        if current is TaskStatus.QUEUED:
            transition = _Transition(
                target=TaskStatus.PAUSED,
                desired_state=DesiredTaskState.PAUSE,
                event_kind="task.paused",
            )
        elif current is TaskStatus.RUNNING:
            transition = _Transition(
                target=TaskStatus.PAUSE_REQUESTED,
                desired_state=DesiredTaskState.PAUSE,
                event_kind="task.pause_requested",
            )
        else:
            raise self._conflict("pause", current)
        return self._apply(
            session,
            task,
            current,
            transition,
            pause_origin=origin,
        )

    def resume(self, session: Session, task_id: str) -> StudyTask:
        task, current = self._load(session, task_id)
        if current in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            return task
        if current is TaskStatus.PAUSED:
            transition = _Transition(
                target=TaskStatus.QUEUED,
                desired_state=DesiredTaskState.RUN,
                event_kind="task.resumed",
            )
        elif current is TaskStatus.PAUSE_REQUESTED:
            transition = _Transition(
                target=TaskStatus.RUNNING,
                desired_state=DesiredTaskState.RUN,
                event_kind="task.pause_canceled",
            )
        else:
            raise self._conflict("resume", current)
        return self._apply(
            session,
            task,
            current,
            transition,
            pause_origin=None,
        )

    def cancel(self, session: Session, task_id: str) -> StudyTask:
        task, current = self._load(session, task_id)
        if current in {TaskStatus.CANCELED, TaskStatus.CANCEL_REQUESTED}:
            return task
        if current in {TaskStatus.QUEUED, TaskStatus.PAUSED, TaskStatus.RECOVERING}:
            transition = _Transition(
                target=TaskStatus.CANCELED,
                desired_state=DesiredTaskState.CANCEL,
                event_kind="task.canceled",
                terminal=True,
            )
        elif current in {TaskStatus.RUNNING, TaskStatus.PAUSE_REQUESTED}:
            transition = _Transition(
                target=TaskStatus.CANCEL_REQUESTED,
                desired_state=DesiredTaskState.CANCEL,
                event_kind="task.cancel_requested",
            )
        else:
            raise self._conflict("cancel", current)
        result = self._apply(
            session,
            task,
            current,
            transition,
            pause_origin=None,
        )
        if transition.terminal:
            session.execute(
                update(TaskChapter)
                .where(
                    TaskChapter.task_id == task_id,
                    TaskChapter.status.in_(
                        [ChapterStatus.PENDING.value, ChapterStatus.RUNNING.value]
                    ),
                )
                .values(status=ChapterStatus.CANCELED.value, finished_at=utc_now())
            )
            session.flush()
            session.expire_all()
            refreshed = StudyTaskRepository().get(session, task_id)
            if refreshed is None:
                raise TaskCommandNotFoundError("task not found")
            return refreshed
        return result

    @staticmethod
    def _load(session: Session, task_id: str) -> tuple[StudyTask, TaskStatus]:
        # SQLite uses deferred transactions, so a read followed by a command can
        # otherwise make an idempotent early return from a stale identity-map
        # object.  Start every command with a no-op write to serialize it with
        # concurrent task commands, then force the reread to use the committed
        # row that was current when this command acquired the write lock.
        result = cast(
            CursorResult[Any],
            session.execute(
                update(StudyTask)
                .where(StudyTask.id == task_id)
                .values(updated_at=StudyTask.updated_at)
            ),
        )
        if result.rowcount != 1:
            raise TaskCommandNotFoundError("task not found")
        session.expire_all()
        task = StudyTaskRepository().get(session, task_id)
        if task is None:
            raise TaskCommandNotFoundError("task not found")
        return task, TaskStatus(task.status)

    @staticmethod
    def _conflict(command: str, current: TaskStatus) -> TaskCommandConflictError:
        terminal = "terminal " if is_terminal_task(current) else ""
        return TaskCommandConflictError(
            f"cannot {command} a task in {terminal}{current.value} state"
        )

    @staticmethod
    def _apply(
        session: Session,
        task: StudyTask,
        current: TaskStatus,
        transition: _Transition,
        *,
        pause_origin: str | None,
    ) -> StudyTask:
        ensure_task_transition(current, transition.target)
        values: dict[str, object] = {
            "status": transition.target.value,
            "desired_state": transition.desired_state.value,
            "pause_origin": pause_origin,
            "updated_at": utc_now(),
        }
        if transition.target is TaskStatus.QUEUED:
            values["run_after"] = utc_now()
        if transition.terminal:
            values["finished_at"] = utc_now()
        result = cast(
            CursorResult[Any],
            session.execute(
                update(StudyTask)
                .where(StudyTask.id == task.id, StudyTask.status == current.value)
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            raise TaskCommandConflictError("task state changed; retry the command")
        append_event(
            session,
            task_id=task.id,
            account_id=task.account_id,
            kind=transition.event_kind,
            payload={
                "from": current.value,
                "to": transition.target.value,
                **({"origin": pause_origin} if pause_origin else {}),
            },
        )
        session.expire_all()
        refreshed = StudyTaskRepository().get(session, task.id)
        if refreshed is None:
            raise TaskCommandNotFoundError("task not found")
        return refreshed
