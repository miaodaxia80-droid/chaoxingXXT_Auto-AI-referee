from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import TaskStatus
from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import Account, StudyTask, TaskRun
from chaoxing_app.infrastructure.db.task_queue import TaskClaim, complete_task_run
from chaoxing_app.worker.supervisor import SupervisorConfig, WorkerSupervisor


@dataclass
class FakeProcess:
    pid: int = 4100
    exit_code: int | None = None
    terminated: bool = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True


class FakeLauncher:
    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []

    def launch(self, _claim: TaskClaim) -> FakeProcess:
        process = FakeProcess(pid=4100 + len(self.processes))
        self.processes.append(process)
        return process


def test_closed_gate_pauses_then_reopens_the_same_task_record() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        engine = create_database_engine(
            f"sqlite:///{(Path(temp_dir) / 'scheduler.db').as_posix()}"
        )
        create_schema(engine)
        try:
            with Session(engine) as session:
                account = Account(
                    remark="Scheduler",
                    username_hint="scheduler",
                    username_fingerprint="scheduler-fingerprint",
                )
                session.add(account)
                session.flush()
                task = StudyTask(
                    account_id=account.id,
                    course_id="course-1",
                    class_id="class-1",
                    cpi="cpi-1",
                    course_title="Scheduled course",
                    run_after=datetime(2020, 1, 1, tzinfo=UTC),
                )
                session.add(task)
                session.commit()
                task_id = task.id

            gate_open = True
            launcher = FakeLauncher()
            supervisor = WorkerSupervisor(
                engine=engine,
                launcher=launcher,
                config=SupervisorConfig(max_workers=1, lease_duration=timedelta(minutes=1)),
                owner_id="scheduler-test",
                scheduler_gate=lambda _now: gate_open,
            )
            started = datetime.now(UTC)
            claim = supervisor.tick(now=started).claimed[0]

            gate_open = False
            closed_tick = supervisor.tick(now=started + timedelta(seconds=1))
            assert closed_tick.claimed == ()
            assert supervisor.window_paused_task_ids == (task_id,)
            with Session(engine) as session:
                persisted = session.get(StudyTask, task_id)
                assert persisted is not None
                assert persisted.status == TaskStatus.PAUSE_REQUESTED.value

            assert complete_task_run(
                engine,
                claim,
                status=TaskStatus.PAUSED,
                exit_reason="pause requested",
                now=started + timedelta(seconds=2),
            )
            launcher.processes[0].exit_code = 0
            supervisor.tick(now=started + timedelta(seconds=3))

            gate_open = True
            reopened_tick = supervisor.tick(now=started + timedelta(seconds=4))

            assert len(reopened_tick.claimed) == 1
            assert reopened_tick.claimed[0].task_id == task_id
            assert supervisor.window_paused_task_ids == ()
            with Session(engine) as session:
                assert session.scalar(select(func.count(StudyTask.id))) == 1
                assert session.scalar(select(func.count(TaskRun.id))) == 2
        finally:
            engine.dispose()


def test_closed_gate_does_not_claim_queued_work() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        engine = create_database_engine(
            f"sqlite:///{(Path(temp_dir) / 'closed.db').as_posix()}"
        )
        create_schema(engine)
        try:
            with Session(engine) as session:
                account = Account(
                    remark="Closed",
                    username_hint="closed",
                    username_fingerprint="closed-fingerprint",
                )
                session.add(account)
                session.flush()
                session.add(
                    StudyTask(
                        account_id=account.id,
                        course_id="course-1",
                        class_id="class-1",
                        cpi="cpi-1",
                        course_title="Closed window",
                        run_after=datetime(2020, 1, 1, tzinfo=UTC),
                    )
                )
                session.commit()

            supervisor = WorkerSupervisor(
                engine=engine,
                launcher=FakeLauncher(),
                scheduler_gate=lambda _now: False,
            )

            tick = supervisor.tick(now=datetime(2026, 8, 10, 12, tzinfo=UTC))

            assert tick.claimed == ()
            with Session(engine) as session:
                task = session.scalar(select(StudyTask))
                assert task is not None
                assert task.status == TaskStatus.QUEUED.value
        finally:
            engine.dispose()


def test_window_pause_survives_a_supervisor_restart() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        engine = create_database_engine(
            f"sqlite:///{(Path(temp_dir) / 'restart.db').as_posix()}"
        )
        create_schema(engine)
        try:
            with Session(engine) as session:
                account = Account(
                    remark="Restart",
                    username_hint="restart",
                    username_fingerprint="restart-fingerprint",
                )
                session.add(account)
                session.flush()
                task = StudyTask(
                    account_id=account.id,
                    course_id="course-1",
                    class_id="class-1",
                    cpi="cpi-1",
                    course_title="Restart window",
                    run_after=datetime(2020, 1, 1, tzinfo=UTC),
                )
                session.add(task)
                session.commit()
                task_id = task.id

            launcher = FakeLauncher()
            first_supervisor = WorkerSupervisor(
                engine=engine,
                launcher=launcher,
                scheduler_gate=lambda _now: True,
                owner_id="before-restart",
            )
            started = datetime.now(UTC)
            claim = first_supervisor.tick(now=started).claimed[0]
            first_supervisor._scheduler_gate = lambda _now: False
            first_supervisor.tick(now=started + timedelta(seconds=1))
            assert first_supervisor.window_paused_task_ids == (task_id,)

            assert complete_task_run(
                engine,
                claim,
                status=TaskStatus.PAUSED,
                exit_reason="pause requested",
                now=started + timedelta(seconds=2),
            )

            replacement_launcher = FakeLauncher()
            replacement = WorkerSupervisor(
                engine=engine,
                launcher=replacement_launcher,
                scheduler_gate=lambda _now: True,
                owner_id="after-restart",
            )
            reopened = replacement.tick(now=started + timedelta(seconds=3))

            assert len(reopened.claimed) == 1
            assert reopened.claimed[0].task_id == task_id
            assert replacement.window_paused_task_ids == ()
            with Session(engine) as session:
                persisted = session.get(StudyTask, task_id)
                assert persisted is not None
                assert persisted.pause_origin is None
                assert session.scalar(select(func.count(StudyTask.id))) == 1
        finally:
            engine.dispose()


def test_closed_gate_pauses_active_lease_owned_by_previous_supervisor() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        engine = create_database_engine(
            f"sqlite:///{(Path(temp_dir) / 'orphan-window.db').as_posix()}"
        )
        create_schema(engine)
        try:
            with Session(engine) as session, session.begin():
                account = Account(
                    remark="Orphan",
                    username_hint="orphan",
                    username_fingerprint="orphan-fingerprint",
                )
                session.add(account)
                session.flush()
                task = StudyTask(
                    account_id=account.id,
                    course_id="course-1",
                    class_id="class-1",
                    cpi="cpi-1",
                    course_title="Orphaned child",
                    run_after=datetime(2020, 1, 1, tzinfo=UTC),
                )
                session.add(task)
                session.flush()
                task_id = task.id

            started = datetime.now(UTC)
            first = WorkerSupervisor(
                engine=engine,
                launcher=FakeLauncher(),
                owner_id="previous-supervisor",
                scheduler_gate=lambda _now: True,
            )
            assert first.tick(now=started).claimed

            replacement = WorkerSupervisor(
                engine=engine,
                launcher=FakeLauncher(),
                owner_id="replacement-supervisor",
                scheduler_gate=lambda _now: False,
            )
            tick = replacement.tick(now=started + timedelta(seconds=1))

            assert tick.claimed == ()
            with Session(engine) as session:
                persisted = session.get(StudyTask, task_id)
                assert persisted is not None
                assert persisted.status == TaskStatus.PAUSE_REQUESTED.value
                assert persisted.pause_origin == "run_window"
        finally:
            engine.dispose()
