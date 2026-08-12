from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import ChapterStatus, TaskStatus
from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import (
    Account,
    AccountLease,
    Event,
    StudyTask,
    TaskChapter,
    TaskRun,
)
from chaoxing_app.infrastructure.db.task_queue import (
    LEASE_EXPIRED_EXIT_REASON,
    claim_next_task,
    complete_task_run,
    is_task_lease_current,
    recover_expired_task_runs,
    renew_task_lease,
)


@pytest.fixture
def task_database() -> Iterator[tuple[Engine, int, str]]:
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "lifecycle.db"
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    create_schema(engine)
    with Session(engine) as session:
        account = Account(
            remark="Lease test",
            username_hint="13*******00",
            username_fingerprint="lease-lifecycle-fingerprint",
        )
        session.add(account)
        session.flush()
        task = StudyTask(
            account_id=account.id,
            course_id="course-1",
            class_id="class-1",
            cpi="10001",
            course_title="Lease lifecycle",
            run_after=datetime(2020, 1, 1, tzinfo=UTC),
        )
        session.add(task)
        session.commit()
        account_id = account.id
        task_id = task.id

    try:
        yield engine, account_id, task_id
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_renewal_records_process_id_and_extends_current_lease(
    task_database: tuple[Engine, int, str],
) -> None:
    engine, account_id, _task_id = task_database
    started_at = datetime(2026, 8, 10, 8, tzinfo=UTC)
    claim = claim_next_task(
        engine,
        owner_id="supervisor-a",
        lease_duration=timedelta(seconds=10),
        now=started_at,
    )
    assert claim is not None
    assert is_task_lease_current(engine, claim, now=started_at + timedelta(seconds=5))

    renewal = renew_task_lease(
        engine,
        claim,
        process_id=4812,
        lease_duration=timedelta(seconds=20),
        now=started_at + timedelta(seconds=5),
    )

    assert renewal is not None
    assert renewal.heartbeat_at == started_at + timedelta(seconds=5)
    assert renewal.lease_expires_at == started_at + timedelta(seconds=25)
    assert is_task_lease_current(engine, claim, now=started_at + timedelta(seconds=24))
    assert not is_task_lease_current(engine, claim, now=started_at + timedelta(seconds=25))
    with Session(engine) as session:
        lease = session.get(AccountLease, account_id)
        run = session.get(TaskRun, claim.run_id)
        assert lease is not None
        assert run is not None
        assert run.process_id == 4812
        assert run.finished_at is None


def test_completion_is_atomic_and_releases_the_account(
    task_database: tuple[Engine, int, str],
) -> None:
    engine, account_id, task_id = task_database
    started_at = datetime(2026, 8, 10, 9, tzinfo=UTC)
    claim = claim_next_task(engine, owner_id="worker-a", now=started_at)
    assert claim is not None

    completed = complete_task_run(
        engine,
        claim,
        status=TaskStatus.SUCCEEDED,
        exit_reason="course completed",
        now=started_at + timedelta(seconds=5),
    )

    assert completed
    assert not complete_task_run(
        engine,
        claim,
        status=TaskStatus.SUCCEEDED,
        now=started_at + timedelta(seconds=6),
    )
    with Session(engine) as session:
        task = session.get(StudyTask, task_id)
        run = session.get(TaskRun, claim.run_id)
        assert task is not None
        assert run is not None
        assert task.status == TaskStatus.SUCCEEDED.value
        assert task.finished_at is not None
        assert run.finished_at is not None
        assert run.exit_reason == "course completed"
        assert session.get(AccountLease, account_id) is None
        kinds = session.scalars(
            select(Event.kind).where(Event.task_id == task_id).order_by(Event.id)
        ).all()
        assert kinds == ["task.claimed", "task.run.completed"]


def test_expired_worker_is_fenced_before_and_after_recovery(
    task_database: tuple[Engine, int, str],
) -> None:
    engine, _account_id, task_id = task_database
    started_at = datetime(2026, 8, 10, 10, tzinfo=UTC)
    old_claim = claim_next_task(
        engine,
        owner_id="worker-old",
        lease_duration=timedelta(seconds=10),
        now=started_at,
    )
    assert old_claim is not None
    expired_at = started_at + timedelta(seconds=11)

    assert renew_task_lease(engine, old_claim, now=expired_at) is None
    assert not complete_task_run(
        engine,
        old_claim,
        status=TaskStatus.SUCCEEDED,
        now=expired_at,
    )
    recovery = recover_expired_task_runs(
        engine,
        max_recovery_attempts=2,
        now=expired_at,
    )
    assert len(recovery) == 1
    assert recovery[0].status is TaskStatus.QUEUED
    assert recovery[0].recovery_attempt == 1

    new_claim = claim_next_task(
        engine,
        owner_id="worker-new",
        lease_duration=timedelta(seconds=30),
        now=expired_at + timedelta(seconds=1),
    )
    assert new_claim is not None
    assert new_claim.task_id == task_id
    assert new_claim.fencing_token == old_claim.fencing_token + 1
    assert renew_task_lease(
        engine,
        old_claim,
        lease_duration=timedelta(minutes=1),
        now=expired_at + timedelta(seconds=2),
    ) is None
    assert not complete_task_run(
        engine,
        old_claim,
        status=TaskStatus.SUCCEEDED,
        now=expired_at + timedelta(seconds=2),
    )
    assert is_task_lease_current(
        engine,
        new_claim,
        now=expired_at + timedelta(seconds=2),
    )
    with Session(engine) as session:
        task = session.get(StudyTask, task_id)
        new_run = session.get(TaskRun, new_claim.run_id)
        old_run = session.get(TaskRun, old_claim.run_id)
        assert task is not None
        assert new_run is not None
        assert old_run is not None
        assert task.status == TaskStatus.RUNNING.value
        assert new_run.finished_at is None
        assert old_run.exit_reason == LEASE_EXPIRED_EXIT_REASON


def test_recovery_limit_fails_repeatedly_crashing_task(
    task_database: tuple[Engine, int, str],
) -> None:
    engine, account_id, task_id = task_database
    started_at = datetime(2026, 8, 10, 11, tzinfo=UTC)
    with Session(engine) as session, session.begin():
        task = session.get(StudyTask, task_id)
        assert task is not None
        task.chapters = [
            TaskChapter(chapter_id="pending", chapter_title="Pending", position=0),
            TaskChapter(
                chapter_id="running",
                chapter_title="Running",
                position=1,
                status=ChapterStatus.RUNNING.value,
            ),
        ]
    first_claim = claim_next_task(
        engine,
        owner_id="worker-1",
        lease_duration=timedelta(seconds=1),
        now=started_at,
    )
    assert first_claim is not None
    first_recovery = recover_expired_task_runs(
        engine,
        max_recovery_attempts=1,
        now=started_at + timedelta(seconds=2),
    )
    assert first_recovery[0].status is TaskStatus.QUEUED

    second_claim = claim_next_task(
        engine,
        owner_id="worker-2",
        lease_duration=timedelta(seconds=1),
        now=started_at + timedelta(seconds=3),
    )
    assert second_claim is not None
    second_recovery = recover_expired_task_runs(
        engine,
        max_recovery_attempts=1,
        now=started_at + timedelta(seconds=5),
    )

    assert second_recovery[0].recovery_attempt == 2
    assert second_recovery[0].status is TaskStatus.FAILED
    with Session(engine) as session:
        task = session.get(StudyTask, task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED.value
        assert task.finished_at is not None
        assert task.last_error is not None
        assert "recovery limit (1) exceeded" in task.last_error
        assert session.get(AccountLease, account_id) is None
        expired_run_count = session.scalar(
            select(func.count(TaskRun.id)).where(
                TaskRun.task_id == task_id,
                TaskRun.exit_reason == LEASE_EXPIRED_EXIT_REASON,
            )
        )
        assert expired_run_count == 2
        chapters = list(
            session.scalars(select(TaskChapter).where(TaskChapter.task_id == task_id))
        )
        assert {chapter.status for chapter in chapters} == {ChapterStatus.FAILED.value}


def test_failed_run_validation_rolls_back_lease_renewal(
    task_database: tuple[Engine, int, str],
) -> None:
    engine, account_id, _task_id = task_database
    started_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
    claim = claim_next_task(
        engine,
        owner_id="worker-a",
        lease_duration=timedelta(seconds=30),
        now=started_at,
    )
    assert claim is not None
    with Session(engine) as session:
        session.execute(
            update(TaskRun)
            .where(TaskRun.id == claim.run_id)
            .values(finished_at=started_at + timedelta(seconds=1))
        )
        session.commit()

    assert renew_task_lease(
        engine,
        claim,
        lease_duration=timedelta(minutes=5),
        now=started_at + timedelta(seconds=2),
    ) is None
    with Session(engine) as session:
        lease = session.get(AccountLease, account_id)
        assert lease is not None
        # SQLite drops timezone information on round-trip; the instant is unchanged.
        assert lease.expires_at.replace(tzinfo=UTC) == claim.lease_expires_at


@pytest.mark.parametrize(
    ("requested_status", "expected_status", "expected_event"),
    [
        (TaskStatus.PAUSE_REQUESTED, TaskStatus.PAUSED, "task.paused"),
        (TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELED, "task.canceled"),
    ],
)
def test_expired_requested_run_settles_without_retry(
    task_database: tuple[Engine, int, str],
    requested_status: TaskStatus,
    expected_status: TaskStatus,
    expected_event: str,
) -> None:
    engine, account_id, task_id = task_database
    started_at = datetime(2026, 8, 10, 13, tzinfo=UTC)
    claim = claim_next_task(
        engine,
        owner_id="worker-a",
        lease_duration=timedelta(seconds=1),
        now=started_at,
    )
    assert claim is not None
    with Session(engine) as session:
        task = session.get(StudyTask, task_id)
        assert task is not None
        task.status = requested_status.value
        task.chapters = [
            TaskChapter(chapter_id="pending", chapter_title="Pending", position=0),
            TaskChapter(
                chapter_id="running",
                chapter_title="Running",
                position=1,
                status=ChapterStatus.RUNNING.value,
            ),
        ]
        session.commit()

    outcomes = recover_expired_task_runs(engine, now=started_at + timedelta(seconds=2))

    assert outcomes[0].status is expected_status
    with Session(engine) as session:
        task = session.get(StudyTask, task_id)
        assert task is not None
        assert task.status == expected_status.value
        assert session.get(AccountLease, account_id) is None
        kinds = set(session.scalars(select(Event.kind).where(Event.task_id == task_id)))
        assert expected_event in kinds
        chapters = list(
            session.scalars(select(TaskChapter).where(TaskChapter.task_id == task_id))
        )
        expected_chapter_status = (
            ChapterStatus.CANCELED.value
            if expected_status is TaskStatus.CANCELED
            else ChapterStatus.PENDING.value
        )
        assert {chapter.status for chapter in chapters} == {expected_chapter_status}
