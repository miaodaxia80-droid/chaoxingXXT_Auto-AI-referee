from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import DesiredTaskState, TaskStatus
from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import Account, StudyTask, TaskRun
from chaoxing_app.infrastructure.db.task_queue import TaskClaim
from chaoxing_app.worker.control import (
    ControlSignal,
    TaskCancelRequested,
    TaskPauseRequested,
    WorkerControl,
)
from chaoxing_app.worker.supervisor import SupervisorConfig, WorkerSupervisor


@dataclass
class FakeProcess:
    pid: int
    exit_code: int | None = None
    terminated: bool = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True


class FakeLauncher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.processes: list[FakeProcess] = []

    def launch(self, claim: TaskClaim) -> FakeProcess:
        if self.fail:
            raise RuntimeError("fixture launch failure")
        process = FakeProcess(pid=4000 + len(self.processes))
        self.processes.append(process)
        return process


@dataclass
class BlockingProcess(FakeProcess):
    block_poll: bool = False

    def __post_init__(self) -> None:
        self.poll_started = Event()
        self.poll_release = Event()

    def poll(self) -> int | None:
        if self.block_poll:
            self.poll_started.set()
            if not self.poll_release.wait(timeout=5):
                raise TimeoutError("test did not release process poll")
        return self.exit_code


class BlockingLauncher:
    def __init__(self) -> None:
        self.processes: list[BlockingProcess] = []

    def launch(self, _claim: TaskClaim) -> BlockingProcess:
        process = BlockingProcess(pid=5000 + len(self.processes))
        self.processes.append(process)
        return process


def database_with_tasks(account_count: int = 2):
    temp_dir = tempfile.TemporaryDirectory()
    engine = create_database_engine(
        f"sqlite:///{(Path(temp_dir.name) / 'supervisor.db').as_posix()}"
    )
    create_schema(engine)
    with Session(engine) as session:
        for index in range(account_count):
            account = Account(
                remark=f"Account {index}",
                username_hint=f"user-{index}",
                username_fingerprint=f"fingerprint-{index}",
            )
            session.add(account)
            session.flush()
            session.add(
                StudyTask(
                    account_id=account.id,
                    course_id=f"course-{index}",
                    class_id=f"class-{index}",
                    cpi=f"cpi-{index}",
                    course_title=f"Course {index}",
                    run_after=datetime(2020, 1, 1, tzinfo=UTC),
                )
            )
        session.commit()
    return temp_dir, engine


def test_supervisor_claims_up_to_capacity_and_records_process_ids() -> None:
    temp_dir, engine = database_with_tasks(3)
    try:
        launcher = FakeLauncher()
        supervisor = WorkerSupervisor(
            engine=engine,
            launcher=launcher,
            config=SupervisorConfig(max_workers=2),
            owner_id="test-supervisor",
        )
        tick = supervisor.tick(now=datetime(2026, 8, 10, 8, tzinfo=UTC))

        assert len(tick.claimed) == 2
        assert len(supervisor.active_task_ids) == 2
        with Session(engine) as session:
            process_ids = list(
                session.scalars(select(TaskRun.process_id).order_by(TaskRun.process_id))
            )
            assert process_ids == [4000, 4001]
            running_count = sum(
                task.status == TaskStatus.RUNNING.value
                for task in session.scalars(select(StudyTask))
            )
            assert running_count == 2
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_launch_failure_is_terminal_and_does_not_block_other_accounts() -> None:
    temp_dir, engine = database_with_tasks(1)
    try:
        supervisor = WorkerSupervisor(
            engine=engine,
            launcher=FakeLauncher(fail=True),
            owner_id="test-supervisor",
        )
        tick = supervisor.tick(now=datetime(2026, 8, 10, 9, tzinfo=UTC))
        assert len(tick.launch_failures) == 1
        with Session(engine) as session:
            task = session.scalar(select(StudyTask))
            assert task is not None
            assert task.status == TaskStatus.FAILED.value
            assert task.last_error == "worker launch failed: RuntimeError"
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_exited_process_waits_for_fenced_recovery_before_reclaim() -> None:
    temp_dir, engine = database_with_tasks(1)
    try:
        launcher = FakeLauncher()
        supervisor = WorkerSupervisor(
            engine=engine,
            launcher=launcher,
            config=SupervisorConfig(
                max_workers=1,
                lease_duration=timedelta(seconds=5),
                recovery_delay=timedelta(0),
            ),
            owner_id="test-supervisor",
        )
        started = datetime(2026, 8, 10, 10, tzinfo=UTC)
        first = supervisor.tick(now=started)
        assert first.claimed[0].fencing_token == 1
        launcher.processes[0].exit_code = 7

        before_expiry = supervisor.tick(now=started + timedelta(seconds=4))
        assert before_expiry.claimed == ()
        assert len(supervisor.active_task_ids) == 1
        after_expiry = supervisor.tick(now=started + timedelta(seconds=6))
        assert len(after_expiry.recoveries) == 1
        assert after_expiry.reaped_task_ids == (first.claimed[0].task_id,)
        assert len(after_expiry.claimed) == 1
        assert after_expiry.claimed[0].fencing_token == 2
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_worker_control_surfaces_pause_cancel_and_continue() -> None:
    temp_dir, engine = database_with_tasks(1)
    try:
        launcher = FakeLauncher()
        supervisor = WorkerSupervisor(
            engine=engine,
            launcher=launcher,
            owner_id="test-supervisor",
        )
        now = datetime(2026, 8, 10, 11, tzinfo=UTC)
        claim = supervisor.tick(now=now).claimed[0]
        control = WorkerControl(engine=engine, claim=claim, now=lambda: now)
        assert control.inspect() is ControlSignal.CONTINUE

        with Session(engine) as session:
            task = session.get(StudyTask, claim.task_id)
            assert task is not None
            task.status = TaskStatus.PAUSE_REQUESTED.value
            task.desired_state = DesiredTaskState.PAUSE.value
            session.commit()
        assert control.inspect() is ControlSignal.PAUSE
        try:
            control.checkpoint()
        except TaskPauseRequested:
            pass
        else:
            raise AssertionError("pause checkpoint did not stop")

        with Session(engine) as session:
            task = session.get(StudyTask, claim.task_id)
            assert task is not None
            task.status = TaskStatus.CANCEL_REQUESTED.value
            task.desired_state = DesiredTaskState.CANCEL.value
            session.commit()
        assert control.inspect() is ControlSignal.CANCEL
        try:
            control.checkpoint()
        except TaskCancelRequested:
            pass
        else:
            raise AssertionError("cancel checkpoint did not stop")
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_supervisor_shutdown_requests_pause_without_claiming_more_work() -> None:
    temp_dir, engine = database_with_tasks(2)
    try:
        launcher = FakeLauncher()
        supervisor = WorkerSupervisor(
            engine=engine,
            launcher=launcher,
            config=SupervisorConfig(max_workers=1),
            owner_id="test-supervisor",
        )
        now = datetime(2026, 8, 10, 12, tzinfo=UTC)
        first = supervisor.tick(now=now)
        assert len(first.claimed) == 1

        assert supervisor.request_pause_all() == (first.claimed[0].task_id,)
        with Session(engine) as session:
            running = session.get(StudyTask, first.claimed[0].task_id)
            assert running is not None
            assert running.status == TaskStatus.PAUSE_REQUESTED.value
            assert running.desired_state == DesiredTaskState.PAUSE.value

        no_claim = supervisor.tick(now=now, claim_new=False)
        assert no_claim.claimed == ()
        assert len(supervisor.active_task_ids) == 1

        assert supervisor.terminate_all() == (first.claimed[0].task_id,)
        assert launcher.processes[0].terminated is True
        assert supervisor.active_task_ids == ()
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_supervisor_state_access_is_safe_during_a_concurrent_reap() -> None:
    temp_dir, engine = database_with_tasks(1)
    try:
        launcher = BlockingLauncher()
        supervisor = WorkerSupervisor(
            engine=engine,
            launcher=launcher,
            config=SupervisorConfig(
                max_workers=1,
                lease_duration=timedelta(seconds=5),
                recovery_delay=timedelta(0),
            ),
            owner_id="concurrent-supervisor",
        )
        started = datetime(2026, 8, 10, 13, tzinfo=UTC)
        claim = supervisor.tick(now=started).claimed[0]
        process = launcher.processes[0]
        process.exit_code = 0
        process.block_poll = True
        terminate_started = Event()

        def terminate() -> tuple[str, ...]:
            terminate_started.set()
            return supervisor.terminate_all()

        with ThreadPoolExecutor(max_workers=3) as executor:
            tick_future = executor.submit(
                supervisor.tick,
                now=started + timedelta(seconds=6),
                claim_new=False,
            )
            assert process.poll_started.wait(timeout=2)

            active_future = executor.submit(lambda: supervisor.active_task_ids)
            assert active_future.result(timeout=2) == (claim.task_id,)

            terminate_future = executor.submit(terminate)
            assert terminate_started.wait(timeout=2)
            assert not terminate_future.done()

            process.poll_release.set()
            tick = tick_future.result(timeout=5)
            terminated = terminate_future.result(timeout=5)

        assert tick.reaped_task_ids == (claim.task_id,)
        assert terminated == ()
        assert supervisor.active_task_ids == ()
    finally:
        engine.dispose()
        temp_dir.cleanup()
