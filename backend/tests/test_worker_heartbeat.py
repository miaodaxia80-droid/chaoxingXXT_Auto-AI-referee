from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import Account, AccountLease, StudyTask, TaskRun, utc_now
from chaoxing_app.infrastructure.db.task_queue import claim_next_task
from chaoxing_app.worker.heartbeat import LeaseHeartbeat


def claimed_database():
    temp_dir = tempfile.TemporaryDirectory()
    engine = create_database_engine(
        f"sqlite:///{(Path(temp_dir.name) / 'heartbeat.db').as_posix()}"
    )
    create_schema(engine)
    with Session(engine) as session:
        account = Account(
            username_hint="fixture",
            username_fingerprint="heartbeat-fingerprint",
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
    claim = claim_next_task(engine, owner_id="heartbeat-test")
    assert claim is not None
    return temp_dir, engine, claim


def test_heartbeat_pulse_extends_lease_and_records_process() -> None:
    temp_dir, engine, claim = claimed_database()
    try:
        heartbeat = LeaseHeartbeat(
            engine=engine,
            claim=claim,
            process_id=4321,
            interval=timedelta(seconds=5),
            lease_duration=timedelta(seconds=20),
        )
        assert heartbeat.pulse()
        assert heartbeat.last_renewal is not None
        assert heartbeat.lost_lease is False
        with Session(engine) as session:
            run = session.get(TaskRun, claim.run_id)
            assert run is not None
            assert run.process_id == 4321
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_heartbeat_stops_after_lease_is_fenced() -> None:
    temp_dir, engine, claim = claimed_database()
    try:
        heartbeat = LeaseHeartbeat(engine=engine, claim=claim)
        with Session(engine) as session:
            session.execute(delete(AccountLease).where(AccountLease.account_id == claim.account_id))
            session.commit()
        assert heartbeat.pulse() is False
        assert heartbeat.lost_lease is True
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_heartbeat_configuration_requires_renewal_margin() -> None:
    temp_dir, engine, claim = claimed_database()
    try:
        with pytest.raises(ValueError, match="must exceed"):
            LeaseHeartbeat(
                engine=engine,
                claim=claim,
                interval=timedelta(seconds=30),
                lease_duration=timedelta(seconds=30),
            )
    finally:
        engine.dispose()
        temp_dir.cleanup()
