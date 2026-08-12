from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Engine, select

from chaoxing_app.domain.tasks import DesiredTaskState, TaskStatus
from chaoxing_app.infrastructure.db.models import StudyTask, utc_now
from chaoxing_app.infrastructure.db.task_queue import TaskClaim, is_task_lease_current


class ControlSignal(StrEnum):
    CONTINUE = "continue"
    PAUSE = "pause"
    CANCEL = "cancel"
    LOST_LEASE = "lost_lease"


class TaskPauseRequested(Exception):
    pass


class TaskCancelRequested(Exception):
    pass


class TaskLeaseLost(Exception):
    pass


class WorkerControl:
    def __init__(
        self,
        *,
        engine: Engine,
        claim: TaskClaim,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._claim = claim
        self._now = now

    def inspect(self) -> ControlSignal:
        if not is_task_lease_current(self._engine, self._claim, now=self._now()):
            return ControlSignal.LOST_LEASE
        with self._engine.connect() as connection:
            state = connection.execute(
                select(StudyTask.status, StudyTask.desired_state).where(
                    StudyTask.id == self._claim.task_id,
                    StudyTask.account_id == self._claim.account_id,
                )
            ).one_or_none()
        if state is None:
            return ControlSignal.LOST_LEASE
        status = TaskStatus(state.status)
        desired = DesiredTaskState(state.desired_state)
        if desired is DesiredTaskState.CANCEL or status is TaskStatus.CANCEL_REQUESTED:
            return ControlSignal.CANCEL
        if desired is DesiredTaskState.PAUSE or status is TaskStatus.PAUSE_REQUESTED:
            return ControlSignal.PAUSE
        return ControlSignal.CONTINUE

    def checkpoint(self) -> None:
        signal = self.inspect()
        if signal is ControlSignal.PAUSE:
            raise TaskPauseRequested
        if signal is ControlSignal.CANCEL:
            raise TaskCancelRequested
        if signal is ControlSignal.LOST_LEASE:
            raise TaskLeaseLost
