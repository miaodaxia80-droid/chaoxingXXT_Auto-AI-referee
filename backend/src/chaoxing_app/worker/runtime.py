from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol

from sqlalchemy import Engine

from chaoxing_app.domain.tasks import TaskStatus, is_terminal_task
from chaoxing_app.infrastructure.db.task_queue import (
    TaskClaim,
    complete_task_run,
)
from chaoxing_app.worker.control import (
    TaskCancelRequested,
    TaskLeaseLost,
    TaskPauseRequested,
    WorkerControl,
)
from chaoxing_app.worker.heartbeat import LeaseHeartbeat


class TaskExecutor(Protocol):
    def execute(self, claim: TaskClaim, control: WorkerControl) -> TaskStatus: ...


class WorkerExit(StrEnum):
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELED = "canceled"
    FAILED = "failed"
    LOST_LEASE = "lost_lease"


@dataclass(frozen=True, slots=True)
class WorkerResult:
    exit: WorkerExit
    status: TaskStatus | None
    persisted: bool


class WorkerRuntime:
    def __init__(
        self,
        *,
        engine: Engine,
        claim: TaskClaim,
        executor: TaskExecutor,
        heartbeat_interval: timedelta = timedelta(seconds=10),
        lease_duration: timedelta = timedelta(seconds=30),
    ) -> None:
        self._engine = engine
        self._claim = claim
        self._executor = executor
        self._heartbeat_interval = heartbeat_interval
        self._lease_duration = lease_duration

    def run(self) -> WorkerResult:
        heartbeat = LeaseHeartbeat(
            engine=self._engine,
            claim=self._claim,
            interval=self._heartbeat_interval,
            lease_duration=self._lease_duration,
        )
        if not heartbeat.start():
            return WorkerResult(WorkerExit.LOST_LEASE, None, False)
        control = WorkerControl(engine=self._engine, claim=self._claim)
        try:
            control.checkpoint()
            status = self._executor.execute(self._claim, control)
            if not is_terminal_task(status) or status is TaskStatus.CANCELED:
                raise ValueError(f"executor returned invalid terminal status: {status.value}")
            if heartbeat.lost_lease:
                return WorkerResult(WorkerExit.LOST_LEASE, None, False)
            persisted = complete_task_run(
                self._engine,
                self._claim,
                status=status,
                exit_reason=status.value,
                event_kind="task.worker_completed",
            )
            return WorkerResult(
                WorkerExit.COMPLETED if persisted else WorkerExit.LOST_LEASE,
                status if persisted else None,
                persisted,
            )
        except TaskPauseRequested:
            return self._settle_requested(TaskStatus.PAUSED, WorkerExit.PAUSED)
        except TaskCancelRequested:
            return self._settle_requested(TaskStatus.CANCELED, WorkerExit.CANCELED)
        except TaskLeaseLost:
            return WorkerResult(WorkerExit.LOST_LEASE, None, False)
        except Exception as exc:
            reason = f"worker execution failed: {type(exc).__name__}"
            persisted = complete_task_run(
                self._engine,
                self._claim,
                status=TaskStatus.FAILED,
                exit_reason=reason,
                event_kind="task.worker_failed",
            )
            return WorkerResult(
                WorkerExit.FAILED if persisted else WorkerExit.LOST_LEASE,
                TaskStatus.FAILED if persisted else None,
                persisted,
            )
        finally:
            heartbeat.stop(timeout=2)

    def _settle_requested(self, status: TaskStatus, exit_kind: WorkerExit) -> WorkerResult:
        persisted = complete_task_run(
            self._engine,
            self._claim,
            status=status,
            exit_reason=status.value,
            event_kind=f"task.{status.value}",
        )
        return WorkerResult(
            exit_kind if persisted else WorkerExit.LOST_LEASE,
            status if persisted else None,
            persisted,
        )
