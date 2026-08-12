from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import DesiredTaskState, TaskStatus
from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import Account, AccountLease, Event, StudyTask, utc_now
from chaoxing_app.infrastructure.db.task_queue import TaskClaim, claim_next_task
from chaoxing_app.worker.control import WorkerControl
from chaoxing_app.worker.runtime import WorkerExit, WorkerRuntime


@dataclass
class ReturningExecutor:
    status: TaskStatus

    def execute(self, _claim: TaskClaim, control: WorkerControl) -> TaskStatus:
        control.checkpoint()
        return self.status


class SecretFailureExecutor:
    def execute(self, _claim: TaskClaim, _control: WorkerControl) -> TaskStatus:
        raise RuntimeError("password=must-not-be-persisted")


def claimed_database():
    temp_dir = tempfile.TemporaryDirectory()
    engine = create_database_engine(
        f"sqlite:///{(Path(temp_dir.name) / 'runtime.db').as_posix()}"
    )
    create_schema(engine)
    with Session(engine) as session:
        account = Account(
            username_hint="fixture",
            username_fingerprint="runtime-fingerprint",
        )
        session.add(account)
        session.flush()
        session.add(
            StudyTask(
                account_id=account.id,
                course_id="course",
                class_id="class",
                cpi="cpi",
                course_title="Course",
                run_after=utc_now() - timedelta(minutes=1),
            )
        )
        session.commit()
    claim = claim_next_task(engine, owner_id="runtime-test")
    assert claim is not None
    return temp_dir, engine, claim


def run_runtime(engine, claim, executor):
    return WorkerRuntime(
        engine=engine,
        claim=claim,
        executor=executor,
        heartbeat_interval=timedelta(seconds=1),
        lease_duration=timedelta(seconds=10),
    ).run()


@pytest.mark.parametrize("status", [TaskStatus.SUCCEEDED, TaskStatus.NEEDS_ATTENTION])
def test_runtime_persists_valid_executor_outcomes(status: TaskStatus) -> None:
    temp_dir, engine, claim = claimed_database()
    try:
        result = run_runtime(engine, claim, ReturningExecutor(status))
        assert result.exit is WorkerExit.COMPLETED
        assert result.status is status
        assert result.persisted
        with Session(engine) as session:
            task = session.get(StudyTask, claim.task_id)
            assert task is not None
            assert task.status == status.value
            assert session.get(AccountLease, claim.account_id) is None
    finally:
        engine.dispose()
        temp_dir.cleanup()


@pytest.mark.parametrize(
    ("requested_status", "desired_state", "expected_status", "expected_exit"),
    [
        (
            TaskStatus.PAUSE_REQUESTED,
            DesiredTaskState.PAUSE,
            TaskStatus.PAUSED,
            WorkerExit.PAUSED,
        ),
        (
            TaskStatus.CANCEL_REQUESTED,
            DesiredTaskState.CANCEL,
            TaskStatus.CANCELED,
            WorkerExit.CANCELED,
        ),
    ],
)
def test_runtime_settles_control_requests(
    requested_status: TaskStatus,
    desired_state: DesiredTaskState,
    expected_status: TaskStatus,
    expected_exit: WorkerExit,
) -> None:
    temp_dir, engine, claim = claimed_database()
    try:
        with Session(engine) as session:
            task = session.get(StudyTask, claim.task_id)
            assert task is not None
            task.status = requested_status.value
            task.desired_state = desired_state.value
            session.commit()
        result = run_runtime(engine, claim, ReturningExecutor(TaskStatus.SUCCEEDED))
        assert result.exit is expected_exit
        assert result.status is expected_status
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_runtime_failure_redacts_exception_message() -> None:
    temp_dir, engine, claim = claimed_database()
    try:
        result = run_runtime(engine, claim, SecretFailureExecutor())
        assert result.exit is WorkerExit.FAILED
        with Session(engine) as session:
            task = session.get(StudyTask, claim.task_id)
            assert task is not None
            assert task.last_error == "worker execution failed: RuntimeError"
            events = list(session.scalars(select(Event).where(Event.task_id == claim.task_id)))
            serialized = " ".join(str(event.payload) for event in events)
            assert "must-not-be-persisted" not in serialized
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_runtime_cannot_persist_after_lease_is_lost() -> None:
    temp_dir, engine, claim = claimed_database()
    try:
        with Session(engine) as session:
            lease = session.get(AccountLease, claim.account_id)
            assert lease is not None
            session.delete(lease)
            session.commit()
        result = run_runtime(engine, claim, ReturningExecutor(TaskStatus.SUCCEEDED))
        assert result.exit is WorkerExit.LOST_LEASE
        assert result.persisted is False
        with Session(engine) as session:
            task = session.get(StudyTask, claim.task_id)
            assert task is not None
            assert task.status == TaskStatus.RUNNING.value
    finally:
        engine.dispose()
        temp_dir.cleanup()
