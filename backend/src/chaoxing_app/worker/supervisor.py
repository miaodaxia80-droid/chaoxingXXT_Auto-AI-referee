from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock, RLock
from typing import Protocol
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import TaskStatus
from chaoxing_app.infrastructure.db.models import AccountLease, StudyTask
from chaoxing_app.infrastructure.db.task_commands import (
    TaskCommandConflictError,
    TaskCommandNotFoundError,
    TaskCommandService,
)
from chaoxing_app.infrastructure.db.task_queue import (
    TaskClaim,
    TaskRecovery,
    claim_next_task,
    complete_task_run,
    is_task_lease_current,
    recover_expired_task_runs,
    renew_task_lease,
)


class ProcessHandle(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...


class ProcessLauncher(Protocol):
    def launch(self, claim: TaskClaim) -> ProcessHandle: ...


@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    max_workers: int = 2
    lease_duration: timedelta = timedelta(seconds=30)
    max_recovery_attempts: int = 3
    recovery_delay: timedelta = timedelta(seconds=2)

    def __post_init__(self) -> None:
        if self.max_workers < 1:
            raise ValueError("max_workers must be positive")
        if self.lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if self.max_recovery_attempts < 0:
            raise ValueError("max_recovery_attempts must not be negative")
        if self.recovery_delay < timedelta(0):
            raise ValueError("recovery_delay must not be negative")


@dataclass(frozen=True, slots=True)
class SupervisorTick:
    recoveries: tuple[TaskRecovery, ...]
    claimed: tuple[TaskClaim, ...]
    reaped_task_ids: tuple[str, ...]
    launch_failures: tuple[str, ...]


@dataclass(slots=True)
class _RunningProcess:
    claim: TaskClaim
    process: ProcessHandle
    exit_code: int | None = field(default=None)


class WorkerSupervisor:
    def __init__(
        self,
        *,
        engine: Engine,
        launcher: ProcessLauncher,
        config: SupervisorConfig | None = None,
        owner_id: str | None = None,
        scheduler_gate: Callable[[datetime | None], bool] | None = None,
    ) -> None:
        self._engine = engine
        self._launcher = launcher
        self._config = config or SupervisorConfig()
        self._owner_id = owner_id or f"supervisor-{uuid4()}"
        if not self._owner_id.strip():
            raise ValueError("owner_id must not be blank")
        self._scheduler_gate = scheduler_gate
        self._operation_lock = RLock()
        self._running_lock = Lock()
        self._running: dict[str, _RunningProcess] = {}

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        with self._running_lock:
            return tuple(self._running)

    @property
    def max_workers(self) -> int:
        return self._config.max_workers

    @property
    def window_paused_task_ids(self) -> tuple[str, ...]:
        with Session(self._engine) as session:
            return tuple(
                session.scalars(
                    select(StudyTask.id)
                    .where(
                        StudyTask.pause_origin == "run_window",
                        StudyTask.status.in_(
                            [
                                TaskStatus.PAUSED.value,
                                TaskStatus.PAUSE_REQUESTED.value,
                            ]
                        ),
                    )
                    .order_by(StudyTask.id)
                )
            )

    def tick(
        self,
        *,
        now: datetime | None = None,
        claim_new: bool = True,
    ) -> SupervisorTick:
        with self._operation_lock:
            return self._tick(now=now, claim_new=claim_new)

    def _tick(
        self,
        *,
        now: datetime | None,
        claim_new: bool,
    ) -> SupervisorTick:
        scheduler_open = self._scheduler_gate(now) if self._scheduler_gate is not None else True
        if claim_new and not scheduler_open:
            self._request_pause_all(origin="run_window")
        elif claim_new:
            self._resume_window_paused_tasks()

        recoveries = tuple(
            recover_expired_task_runs(
                self._engine,
                max_recovery_attempts=self._config.max_recovery_attempts,
                retry_delay=self._config.recovery_delay,
                now=now,
            )
        )
        reaped: list[str] = []
        with self._running_lock:
            running_processes = tuple(self._running.items())
        for task_id, running in running_processes:
            exit_code = running.process.poll()
            if exit_code is None:
                if not is_task_lease_current(self._engine, running.claim, now=now):
                    running.process.terminate()
                    with self._running_lock:
                        if self._running.get(task_id) is running:
                            del self._running[task_id]
                    reaped.append(task_id)
                continue
            running.exit_code = exit_code
            if not is_task_lease_current(self._engine, running.claim, now=now):
                with self._running_lock:
                    if self._running.get(task_id) is running:
                        del self._running[task_id]
                reaped.append(task_id)

        claimed: list[TaskClaim] = []
        launch_failures: list[str] = []
        while claim_new and scheduler_open and self._has_capacity():
            claim = claim_next_task(
                self._engine,
                owner_id=self._owner_id,
                lease_duration=self._config.lease_duration,
                now=now,
            )
            if claim is None:
                break
            try:
                process = self._launcher.launch(claim)
            except Exception as exc:
                complete_task_run(
                    self._engine,
                    claim,
                    status=TaskStatus.FAILED,
                    exit_reason=f"worker launch failed: {type(exc).__name__}",
                    event_kind="task.worker_launch_failed",
                    now=now,
                )
                launch_failures.append(claim.task_id)
                continue
            renewal = renew_task_lease(
                self._engine,
                claim,
                lease_duration=self._config.lease_duration,
                process_id=process.pid,
                now=now,
            )
            if renewal is None:
                process.terminate()
                launch_failures.append(claim.task_id)
                continue
            with self._running_lock:
                self._running[claim.task_id] = _RunningProcess(claim=claim, process=process)
            claimed.append(claim)

        return SupervisorTick(
            recoveries=recoveries,
            claimed=tuple(claimed),
            reaped_task_ids=tuple(reaped),
            launch_failures=tuple(launch_failures),
        )

    def _has_capacity(self) -> bool:
        with self._running_lock:
            return len(self._running) < self._config.max_workers

    def request_pause_all(self, *, origin: str | None = None) -> tuple[str, ...]:
        """Request cooperative pauses without discarding the current task records."""
        with self._operation_lock:
            return self._request_pause_all(origin=origin)

    def _request_pause_all(self, *, origin: str | None) -> tuple[str, ...]:
        requested: list[str] = []
        command = TaskCommandService()
        with self._running_lock:
            task_ids = set(self._running)
        if origin == "run_window":
            # A replacement supervisor cannot see child processes launched by the
            # previous parent, but their leases remain the durable source of truth.
            with Session(self._engine) as session:
                task_ids.update(
                    session.scalars(
                        select(StudyTask.id)
                        .join(AccountLease, AccountLease.task_id == StudyTask.id)
                        .where(StudyTask.status == TaskStatus.RUNNING.value)
                    )
                )
        for task_id in sorted(task_ids):
            with Session(self._engine) as session:
                try:
                    task = session.get(StudyTask, task_id)
                    if task is None:
                        continue
                    if task.status != TaskStatus.RUNNING.value:
                        continue
                    command.pause(session, task_id, origin=origin)
                except (TaskCommandConflictError, TaskCommandNotFoundError):
                    session.rollback()
                    continue
                session.commit()
                requested.append(task_id)
        return tuple(requested)

    def _resume_window_paused_tasks(self) -> tuple[str, ...]:
        """Resume only tasks this supervisor paused when the gate closed."""

        resumed: list[str] = []
        command = TaskCommandService()
        with Session(self._engine) as session:
            task_ids = tuple(
                session.scalars(
                    select(StudyTask.id).where(
                        StudyTask.pause_origin == "run_window",
                        StudyTask.status.in_(
                            [
                                TaskStatus.PAUSED.value,
                                TaskStatus.PAUSE_REQUESTED.value,
                            ]
                        ),
                    )
                )
            )
        for task_id in task_ids:
            with Session(self._engine) as session:
                try:
                    command.resume(session, task_id)
                except (TaskCommandConflictError, TaskCommandNotFoundError):
                    session.rollback()
                else:
                    session.commit()
                    resumed.append(task_id)
        return tuple(resumed)

    def terminate_all(self) -> tuple[str, ...]:
        """Terminate remaining processes after a cooperative shutdown timeout."""
        with self._operation_lock:
            terminated: list[str] = []
            with self._running_lock:
                running_processes = tuple(self._running.items())
            for task_id, running in running_processes:
                running.process.terminate()
                with self._running_lock:
                    if self._running.get(task_id) is running:
                        del self._running[task_id]
                terminated.append(task_id)
            return tuple(terminated)
