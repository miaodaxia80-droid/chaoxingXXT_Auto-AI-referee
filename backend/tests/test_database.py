import tempfile
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from chaoxing_app.domain.tasks import TaskStatus
from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import Account, AccountLease, AdminUser, StudyTask
from chaoxing_app.infrastructure.db.task_queue import claim_next_task


def make_database():
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "test.db"
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    create_schema(engine)
    return temp_dir, engine


def add_account_and_tasks(engine, task_count: int = 1) -> tuple[int, list[str]]:
    with Session(engine) as session:
        account = Account(
            remark="Test",
            username_hint="13*******00",
            username_fingerprint="fingerprint",
        )
        session.add(account)
        session.flush()
        tasks = [
            StudyTask(
                account_id=account.id,
                course_id=f"course-{index}",
                class_id=f"class-{index}",
                cpi="10001",
                course_title=f"Course {index}",
            )
            for index in range(task_count)
        ]
        session.add_all(tasks)
        session.commit()
        return account.id, [task.id for task in tasks]


def test_schema_enables_foreign_keys() -> None:
    temp_dir, engine = make_database()
    try:
        with engine.connect() as connection:
            enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        assert enabled == 1
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_database_enforces_single_admin_row() -> None:
    temp_dir, engine = make_database()
    try:
        with Session(engine) as session:
            session.add(
                AdminUser(id=2, username="second-admin", password_hash="not-a-real-hash")
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_admin_singleton_migration_preserves_existing_web_session(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    alembic_config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    alembic_config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.as_posix()}",
    )
    command.upgrade(alembic_config, "20260811_0001")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO admin_users (
                    id, username, password_hash, disabled, session_version,
                    created_at, updated_at
                ) VALUES (
                    1, 'admin', 'hash', 0, 1,
                    '2026-08-12 00:00:00', '2026-08-12 00:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO web_sessions (
                    id, admin_id, token_digest, csrf_digest, session_version,
                    created_at, expires_at, last_seen_at, revoked_at
                ) VALUES (
                    'session-1', 1, 'token-digest', 'csrf-digest', 1,
                    '2026-08-12 00:00:00', '2026-08-13 00:00:00',
                    '2026-08-12 00:00:00', NULL
                )
                """
            )
        )
    engine.dispose()

    command.upgrade(alembic_config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id, admin_id FROM web_sessions")).one() == (
                "session-1",
                1,
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO admin_users (
                        id, username, password_hash, disabled, session_version,
                        created_at, updated_at
                    ) VALUES (
                        2, 'second-admin', 'hash', 0, 1,
                        '2026-08-12 00:00:00', '2026-08-12 00:00:00'
                    )
                    """
                )
            )
    finally:
        engine.dispose()


def test_alembic_creates_missing_sqlite_parent_directory(tmp_path: Path) -> None:
    database_path = tmp_path / "new" / "nested" / "migration.db"
    alembic_config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    alembic_config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.as_posix()}",
    )

    assert not database_path.parent.exists()
    command.upgrade(alembic_config, "head")

    assert database_path.is_file()
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert revision == "20260812_0003"
    finally:
        engine.dispose()


def test_task_is_claimed_only_once() -> None:
    temp_dir, engine = make_database()
    try:
        _account_id, task_ids = add_account_and_tasks(engine)
        first = claim_next_task(engine, owner_id="worker-a")
        second = claim_next_task(engine, owner_id="worker-b")

        assert first is not None
        assert first.task_id == task_ids[0]
        assert second is None
        with Session(engine) as session:
            status = session.scalar(select(StudyTask.status).where(StudyTask.id == task_ids[0]))
            assert status == TaskStatus.RUNNING.value
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_same_account_cannot_claim_second_task_while_lease_is_active() -> None:
    temp_dir, engine = make_database()
    try:
        account_id, _task_ids = add_account_and_tasks(engine, task_count=2)
        first = claim_next_task(
            engine,
            owner_id="worker-a",
            lease_duration=timedelta(minutes=1),
        )
        second = claim_next_task(engine, owner_id="worker-b")

        assert first is not None
        assert second is None
        with Session(engine) as session:
            lease = session.get(AccountLease, account_id)
            assert lease is not None
            assert lease.fencing_token == 1
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_disabled_account_is_not_claimed() -> None:
    temp_dir, engine = make_database()
    try:
        account_id, _task_ids = add_account_and_tasks(engine)
        with Session(engine) as session:
            account = session.get(Account, account_id)
            assert account is not None
            account.enabled = False
            session.commit()

        assert claim_next_task(engine, owner_id="worker-a") is None
    finally:
        engine.dispose()
        temp_dir.cleanup()
