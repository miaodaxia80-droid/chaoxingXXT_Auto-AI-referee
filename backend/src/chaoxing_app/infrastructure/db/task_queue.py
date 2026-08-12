from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, and_, delete, exists, func, insert, select, true, update
from sqlalchemy.engine import Connection

from chaoxing_app.domain.tasks import (
    ChapterStatus,
    DesiredTaskState,
    TaskStatus,
    ensure_task_transition,
    is_terminal_task,
)
from chaoxing_app.infrastructure.db.models import (
    Account,
    AccountLease,
    Event,
    StudyTask,
    TaskChapter,
    TaskRun,
    new_public_id,
    utc_now,
)
from chaoxing_app.infrastructure.db.notification_outbox import (
    enqueue_terminal_notifications,
)

LEASE_EXPIRED_EXIT_REASON = "lease_expired"
_LEASED_TASK_STATUSES = (
    TaskStatus.RUNNING.value,
    TaskStatus.PAUSE_REQUESTED.value,
    TaskStatus.CANCEL_REQUESTED.value,
)


@dataclass(frozen=True, slots=True)
class TaskClaim:
    task_id: str
    account_id: int
    run_id: str
    fencing_token: int
    lease_expires_at: datetime
    # Kept last with a default so claims constructed by older callers remain valid.
    owner_id: str = ""


@dataclass(frozen=True, slots=True)
class LeaseRenewal:
    heartbeat_at: datetime
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class TaskRecovery:
    task_id: str
    account_id: int
    run_id: str
    fencing_token: int
    recovery_attempt: int
    status: TaskStatus


class _LeaseLost(Exception):
    pass


@contextmanager
def _immediate_transaction(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()


def _require_positive_duration(value: timedelta, *, name: str) -> None:
    if value <= timedelta(0):
        raise ValueError(f"{name} must be positive")


def _lease_owner_condition(claim: TaskClaim) -> Any:
    if claim.owner_id:
        return AccountLease.owner_id == claim.owner_id
    return true()


def _run_owner_condition(claim: TaskClaim) -> Any:
    if claim.owner_id:
        return TaskRun.worker_id == claim.owner_id
    return true()


def _insert_event(
    connection: Connection,
    *,
    task_id: str,
    account_id: int,
    kind: str,
    occurred_at: datetime,
    payload: Mapping[str, Any],
    level: str = "info",
) -> None:
    connection.execute(
        insert(Event).values(
            task_id=task_id,
            account_id=account_id,
            kind=kind,
            level=level,
            payload=dict(payload),
            occurred_at=occurred_at,
        )
    )


def claim_next_task(
    engine: Engine,
    *,
    owner_id: str,
    lease_duration: timedelta = timedelta(seconds=30),
    now: datetime | None = None,
) -> TaskClaim | None:
    if not owner_id.strip():
        raise ValueError("owner_id must not be blank")
    _require_positive_duration(lease_duration, name="lease_duration")

    with _immediate_transaction(engine) as connection:
        claimed_at = now or utc_now()
        lease_expires_at = claimed_at + lease_duration
        # Expired leases are deliberately not deleted here. Recovery must first
        # close their runs and persist the task transition and audit events.
        has_lease = exists().where(AccountLease.account_id == StudyTask.account_id)
        candidate = connection.execute(
            select(StudyTask.id, StudyTask.account_id)
            .join(Account, Account.id == StudyTask.account_id)
            .where(
                StudyTask.status == TaskStatus.QUEUED.value,
                StudyTask.desired_state == DesiredTaskState.RUN.value,
                StudyTask.run_after <= claimed_at,
                Account.enabled.is_(True),
                ~has_lease,
            )
            .order_by(StudyTask.priority.desc(), StudyTask.created_at, StudyTask.id)
            .limit(1)
        ).one_or_none()
        if candidate is None:
            return None

        connection.execute(
            update(Account)
            .where(Account.id == candidate.account_id)
            .values(lease_version=Account.lease_version + 1)
        )
        fencing_token = connection.execute(
            select(Account.lease_version).where(Account.id == candidate.account_id)
        ).scalar_one()
        run_id = new_public_id()
        connection.execute(
            insert(AccountLease).values(
                account_id=candidate.account_id,
                task_id=candidate.id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                acquired_at=claimed_at,
                expires_at=lease_expires_at,
            )
        )
        connection.execute(
            insert(TaskRun).values(
                id=run_id,
                task_id=candidate.id,
                worker_id=owner_id,
                fencing_token=fencing_token,
                started_at=claimed_at,
                heartbeat_at=claimed_at,
            )
        )
        result = connection.execute(
            update(StudyTask)
            .where(
                and_(
                    StudyTask.id == candidate.id,
                    StudyTask.status == TaskStatus.QUEUED.value,
                )
            )
            .values(status=TaskStatus.RUNNING.value, started_at=claimed_at, finished_at=None)
        )
        if result.rowcount != 1:
            raise _LeaseLost
        _insert_event(
            connection,
            task_id=candidate.id,
            account_id=candidate.account_id,
            kind="task.claimed",
            occurred_at=claimed_at,
            payload={
                "run_id": run_id,
                "worker_id": owner_id,
                "fencing_token": fencing_token,
                "lease_expires_at": lease_expires_at.isoformat(),
            },
        )

    return TaskClaim(
        task_id=candidate.id,
        account_id=candidate.account_id,
        run_id=run_id,
        fencing_token=fencing_token,
        lease_expires_at=lease_expires_at,
        owner_id=owner_id,
    )


def is_task_lease_current(
    engine: Engine,
    claim: TaskClaim,
    *,
    now: datetime | None = None,
) -> bool:
    checked_at = now or utc_now()
    with engine.connect() as connection:
        current = connection.execute(
            select(AccountLease.account_id)
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
            .where(
                AccountLease.account_id == claim.account_id,
                AccountLease.task_id == claim.task_id,
                AccountLease.fencing_token == claim.fencing_token,
                AccountLease.expires_at > checked_at,
                Account.lease_version == claim.fencing_token,
                StudyTask.status.in_(_LEASED_TASK_STATUSES),
                TaskRun.finished_at.is_(None),
                _lease_owner_condition(claim),
                _run_owner_condition(claim),
            )
        ).scalar_one_or_none()
    return current is not None


def renew_task_lease(
    engine: Engine,
    claim: TaskClaim,
    *,
    lease_duration: timedelta = timedelta(seconds=30),
    process_id: int | None = None,
    now: datetime | None = None,
) -> LeaseRenewal | None:
    _require_positive_duration(lease_duration, name="lease_duration")
    if process_id is not None and process_id <= 0:
        raise ValueError("process_id must be positive")

    account_is_current = exists().where(
        Account.id == claim.account_id,
        Account.lease_version == claim.fencing_token,
    )
    task_is_active = exists().where(
        StudyTask.id == claim.task_id,
        StudyTask.account_id == claim.account_id,
        StudyTask.status.in_(_LEASED_TASK_STATUSES),
    )

    try:
        with _immediate_transaction(engine) as connection:
            heartbeat_at = now or utc_now()
            lease_expires_at = heartbeat_at + lease_duration
            lease_result = connection.execute(
                update(AccountLease)
                .where(
                    AccountLease.account_id == claim.account_id,
                    AccountLease.task_id == claim.task_id,
                    AccountLease.fencing_token == claim.fencing_token,
                    AccountLease.expires_at > heartbeat_at,
                    _lease_owner_condition(claim),
                    account_is_current,
                    task_is_active,
                )
                .values(expires_at=lease_expires_at)
            )
            if lease_result.rowcount != 1:
                raise _LeaseLost

            run_values: dict[str, Any] = {"heartbeat_at": heartbeat_at}
            if process_id is not None:
                run_values["process_id"] = process_id
            run_result = connection.execute(
                update(TaskRun)
                .where(
                    TaskRun.id == claim.run_id,
                    TaskRun.task_id == claim.task_id,
                    TaskRun.fencing_token == claim.fencing_token,
                    TaskRun.finished_at.is_(None),
                    _run_owner_condition(claim),
                )
                .values(**run_values)
            )
            if run_result.rowcount != 1:
                raise _LeaseLost
    except _LeaseLost:
        return None

    return LeaseRenewal(heartbeat_at=heartbeat_at, lease_expires_at=lease_expires_at)


def complete_task_run(
    engine: Engine,
    claim: TaskClaim,
    *,
    status: TaskStatus,
    exit_reason: str | None = None,
    event_kind: str = "task.run.completed",
    event_payload: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    try:
        with _immediate_transaction(engine) as connection:
            completed_at = now or utc_now()
            current_row = connection.execute(
                select(StudyTask.status, StudyTask.config_snapshot)
                .join(
                    AccountLease,
                    and_(
                        AccountLease.task_id == StudyTask.id,
                        AccountLease.account_id == StudyTask.account_id,
                    ),
                )
                .join(Account, Account.id == AccountLease.account_id)
                .join(
                    TaskRun,
                    and_(
                        TaskRun.id == claim.run_id,
                        TaskRun.task_id == StudyTask.id,
                        TaskRun.fencing_token == AccountLease.fencing_token,
                    ),
                )
                .where(
                    StudyTask.id == claim.task_id,
                    StudyTask.account_id == claim.account_id,
                    AccountLease.fencing_token == claim.fencing_token,
                    AccountLease.expires_at > completed_at,
                    Account.lease_version == claim.fencing_token,
                    TaskRun.finished_at.is_(None),
                    _lease_owner_condition(claim),
                    _run_owner_condition(claim),
                )
            ).one_or_none()
            if current_row is None:
                raise _LeaseLost

            current_status_value, config_snapshot = current_row
            current_status = TaskStatus(current_status_value)
            ensure_task_transition(current_status, status)
            task_values: dict[str, Any] = {"status": status.value}
            if is_terminal_task(status):
                task_values["finished_at"] = completed_at
                task_values["pause_origin"] = None
            else:
                task_values["finished_at"] = None
            if status is TaskStatus.FAILED and exit_reason:
                task_values["last_error"] = exit_reason
            elif status is TaskStatus.SUCCEEDED:
                task_values["last_error"] = None

            task_result = connection.execute(
                update(StudyTask)
                .where(
                    StudyTask.id == claim.task_id,
                    StudyTask.status == current_status.value,
                )
                .values(**task_values)
            )
            if task_result.rowcount != 1:
                raise _LeaseLost

            resolved_exit_reason = exit_reason or status.value
            run_result = connection.execute(
                update(TaskRun)
                .where(
                    TaskRun.id == claim.run_id,
                    TaskRun.task_id == claim.task_id,
                    TaskRun.fencing_token == claim.fencing_token,
                    TaskRun.finished_at.is_(None),
                    _run_owner_condition(claim),
                )
                .values(finished_at=completed_at, exit_reason=resolved_exit_reason)
            )
            if run_result.rowcount != 1:
                raise _LeaseLost

            if is_terminal_task(status):
                enqueue_terminal_notifications(
                    connection,
                    task_id=claim.task_id,
                    config_snapshot=config_snapshot,
                    occurred_at=completed_at,
                )

            lease_result = connection.execute(
                delete(AccountLease).where(
                    AccountLease.account_id == claim.account_id,
                    AccountLease.task_id == claim.task_id,
                    AccountLease.fencing_token == claim.fencing_token,
                    _lease_owner_condition(claim),
                )
            )
            if lease_result.rowcount != 1:
                raise _LeaseLost

            payload = dict(event_payload or {})
            payload.update(
                {
                    "run_id": claim.run_id,
                    "fencing_token": claim.fencing_token,
                    "status": status.value,
                    "exit_reason": resolved_exit_reason,
                }
            )
            _insert_event(
                connection,
                task_id=claim.task_id,
                account_id=claim.account_id,
                kind=event_kind,
                occurred_at=completed_at,
                payload=payload,
                level="error" if status is TaskStatus.FAILED else "info",
            )
    except _LeaseLost:
        return False

    return True


def release_task_lease(
    engine: Engine,
    claim: TaskClaim,
    *,
    status: TaskStatus,
    exit_reason: str,
    event_payload: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    return complete_task_run(
        engine,
        claim,
        status=status,
        exit_reason=exit_reason,
        event_kind="task.run.released",
        event_payload=event_payload,
        now=now,
    )


def _set_recovery_status(
    connection: Connection,
    *,
    task_id: str,
    account_id: int,
    current_status: TaskStatus,
    target_status: TaskStatus,
    occurred_at: datetime,
    event_kind: str,
    payload: Mapping[str, Any],
    last_error: str | None = None,
    run_after: datetime | None = None,
) -> None:
    ensure_task_transition(current_status, target_status)
    values: dict[str, Any] = {"status": target_status.value}
    if is_terminal_task(target_status):
        values["finished_at"] = occurred_at
    elif target_status is TaskStatus.QUEUED:
        values.update(finished_at=None, run_after=run_after or occurred_at)
    if last_error is not None:
        values["last_error"] = last_error

    result = connection.execute(
        update(StudyTask)
        .where(StudyTask.id == task_id, StudyTask.status == current_status.value)
        .values(**values)
    )
    if result.rowcount != 1:
        raise _LeaseLost
    if is_terminal_task(target_status):
        config_snapshot = connection.execute(
            select(StudyTask.config_snapshot).where(StudyTask.id == task_id)
        ).scalar_one()
        enqueue_terminal_notifications(
            connection,
            task_id=task_id,
            config_snapshot=config_snapshot,
            occurred_at=occurred_at,
        )
    _insert_event(
        connection,
        task_id=task_id,
        account_id=account_id,
        kind=event_kind,
        occurred_at=occurred_at,
        payload=payload,
        level="error" if target_status is TaskStatus.FAILED else "warning",
    )


def _settle_recovered_chapters(
    connection: Connection,
    *,
    task_id: str,
    target_status: TaskStatus,
    occurred_at: datetime,
) -> None:
    """Keep chapter rows aligned with a task settled after a dead worker."""

    runnable = [
        ChapterStatus.PENDING.value,
        ChapterStatus.RUNNING.value,
    ]
    if target_status in {TaskStatus.QUEUED, TaskStatus.PAUSED}:
        connection.execute(
            update(TaskChapter)
            .where(
                TaskChapter.task_id == task_id,
                TaskChapter.status == ChapterStatus.RUNNING.value,
            )
            .values(
                status=ChapterStatus.PENDING.value,
                started_at=None,
                finished_at=None,
                last_error=None,
            )
        )
        return
    if target_status is TaskStatus.CANCELED:
        connection.execute(
            update(TaskChapter)
            .where(TaskChapter.task_id == task_id, TaskChapter.status.in_(runnable))
            .values(
                status=ChapterStatus.CANCELED.value,
                finished_at=occurred_at,
                last_error="task_canceled_after_worker_lease_expired",
            )
        )
    elif target_status is TaskStatus.FAILED:
        connection.execute(
            update(TaskChapter)
            .where(TaskChapter.task_id == task_id, TaskChapter.status.in_(runnable))
            .values(
                status=ChapterStatus.FAILED.value,
                finished_at=occurred_at,
                last_error="worker_lease_expired",
            )
        )
def recover_expired_task_runs(
    engine: Engine,
    *,
    max_recovery_attempts: int = 3,
    retry_delay: timedelta = timedelta(0),
    now: datetime | None = None,
) -> list[TaskRecovery]:
    if max_recovery_attempts < 0:
        raise ValueError("max_recovery_attempts must not be negative")
    if retry_delay < timedelta(0):
        raise ValueError("retry_delay must not be negative")

    recoveries: list[TaskRecovery] = []

    with _immediate_transaction(engine) as connection:
        recovered_at = now or utc_now()
        expired = connection.execute(
            select(
                AccountLease.account_id,
                AccountLease.task_id,
                AccountLease.owner_id,
                AccountLease.fencing_token,
                StudyTask.status,
                TaskRun.id.label("run_id"),
            )
            .join(StudyTask, StudyTask.id == AccountLease.task_id)
            .join(
                TaskRun,
                and_(
                    TaskRun.task_id == AccountLease.task_id,
                    TaskRun.fencing_token == AccountLease.fencing_token,
                    TaskRun.worker_id == AccountLease.owner_id,
                    TaskRun.finished_at.is_(None),
                ),
            )
            .where(AccountLease.expires_at <= recovered_at)
            .order_by(AccountLease.expires_at, AccountLease.account_id)
        ).all()

        for expired_run in expired:
            run_result = connection.execute(
                update(TaskRun)
                .where(
                    TaskRun.id == expired_run.run_id,
                    TaskRun.task_id == expired_run.task_id,
                    TaskRun.fencing_token == expired_run.fencing_token,
                    TaskRun.worker_id == expired_run.owner_id,
                    TaskRun.finished_at.is_(None),
                )
                .values(finished_at=recovered_at, exit_reason=LEASE_EXPIRED_EXIT_REASON)
            )
            if run_result.rowcount != 1:
                raise _LeaseLost

            recovery_attempt = connection.execute(
                select(func.count(TaskRun.id)).where(
                    TaskRun.task_id == expired_run.task_id,
                    TaskRun.exit_reason == LEASE_EXPIRED_EXIT_REASON,
                )
            ).scalar_one()
            lease_result = connection.execute(
                delete(AccountLease).where(
                    AccountLease.account_id == expired_run.account_id,
                    AccountLease.task_id == expired_run.task_id,
                    AccountLease.owner_id == expired_run.owner_id,
                    AccountLease.fencing_token == expired_run.fencing_token,
                    AccountLease.expires_at <= recovered_at,
                )
            )
            if lease_result.rowcount != 1:
                raise _LeaseLost

            event_payload = {
                "run_id": expired_run.run_id,
                "worker_id": expired_run.owner_id,
                "fencing_token": expired_run.fencing_token,
                "recovery_attempt": recovery_attempt,
            }
            _insert_event(
                connection,
                task_id=expired_run.task_id,
                account_id=expired_run.account_id,
                kind="task.lease_expired",
                occurred_at=recovered_at,
                payload=event_payload,
                level="warning",
            )

            current_status = TaskStatus(expired_run.status)
            if current_status is TaskStatus.PAUSE_REQUESTED:
                final_status = TaskStatus.PAUSED
                _set_recovery_status(
                    connection,
                    task_id=expired_run.task_id,
                    account_id=expired_run.account_id,
                    current_status=current_status,
                    target_status=final_status,
                    occurred_at=recovered_at,
                    event_kind="task.paused",
                    payload=event_payload,
                )
            elif current_status is TaskStatus.CANCEL_REQUESTED:
                final_status = TaskStatus.CANCELED
                _set_recovery_status(
                    connection,
                    task_id=expired_run.task_id,
                    account_id=expired_run.account_id,
                    current_status=current_status,
                    target_status=final_status,
                    occurred_at=recovered_at,
                    event_kind="task.canceled",
                    payload=event_payload,
                )
            elif current_status in {TaskStatus.RUNNING, TaskStatus.RECOVERING}:
                if current_status is TaskStatus.RUNNING:
                    _set_recovery_status(
                        connection,
                        task_id=expired_run.task_id,
                        account_id=expired_run.account_id,
                        current_status=current_status,
                        target_status=TaskStatus.RECOVERING,
                        occurred_at=recovered_at,
                        event_kind="task.recovering",
                        payload=event_payload,
                        last_error="worker lease expired",
                    )
                    current_status = TaskStatus.RECOVERING

                if recovery_attempt <= max_recovery_attempts:
                    final_status = TaskStatus.QUEUED
                    _set_recovery_status(
                        connection,
                        task_id=expired_run.task_id,
                        account_id=expired_run.account_id,
                        current_status=current_status,
                        target_status=final_status,
                        occurred_at=recovered_at,
                        event_kind="task.requeued",
                        payload=event_payload,
                        last_error="worker lease expired; task queued for recovery",
                        run_after=recovered_at + retry_delay,
                    )
                else:
                    final_status = TaskStatus.FAILED
                    _set_recovery_status(
                        connection,
                        task_id=expired_run.task_id,
                        account_id=expired_run.account_id,
                        current_status=current_status,
                        target_status=final_status,
                        occurred_at=recovered_at,
                        event_kind="task.failed",
                        payload=event_payload,
                        last_error=(
                            "worker lease expired; "
                            f"recovery limit ({max_recovery_attempts}) exceeded"
                        ),
                    )
            else:
                # A stale lease on an already-settled task should not block its account.
                final_status = current_status

            _settle_recovered_chapters(
                connection,
                task_id=expired_run.task_id,
                target_status=final_status,
                occurred_at=recovered_at,
            )
            recoveries.append(
                TaskRecovery(
                    task_id=expired_run.task_id,
                    account_id=expired_run.account_id,
                    run_id=expired_run.run_id,
                    fencing_token=expired_run.fencing_token,
                    recovery_attempt=recovery_attempt,
                    status=final_status,
                )
            )

    return recoveries
