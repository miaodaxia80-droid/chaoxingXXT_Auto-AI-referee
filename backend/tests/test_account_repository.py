import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.accounts import (
    AccountRepository,
    AccountUpdate,
    DuplicateAccountError,
    NewAccount,
)
from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import (
    Account,
    AccountLease,
    AccountSecret,
    Event,
    StudyTask,
    TaskChapter,
    TaskRun,
)
from chaoxing_app.infrastructure.security.secrets import SecretBox

TEST_KEY = b"k" * 32


def make_repository_database():
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "accounts.db"
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    create_schema(engine)
    repository = AccountRepository(
        secret_box=SecretBox(TEST_KEY),
        fingerprint_key=TEST_KEY,
    )
    return temp_dir, engine, repository


def test_repository_partially_updates_and_explicitly_clears_secrets() -> None:
    temp_dir, engine, repository = make_repository_database()
    try:
        with Session(engine) as session:
            account = repository.create(
                session,
                NewAccount(
                    username="13800138000",
                    password="old-password",
                    cookies="old-cookie",
                    remark="Old remark",
                    user_agent="Old agent",
                ),
            )
            session.commit()
            account_id = account.id
            original_password = account.secret.password_encrypted
            original_cookies = account.secret.cookies_encrypted

            updated = repository.update(
                session,
                account_id,
                AccountUpdate(
                    username="13900139000",
                    password="",
                    cookies="",
                    remark="  Updated remark  ",
                    user_agent="  Updated agent  ",
                    enabled=False,
                ),
            )

            assert updated is not None
            assert updated.username_hint == "139****9000"
            assert repository.reveal_username(updated) == "13900139000"
            assert updated.remark == "Updated remark"
            assert updated.user_agent == "Updated agent"
            assert updated.enabled is False
            assert updated.secret.password_encrypted == original_password
            assert updated.secret.cookies_encrypted == original_cookies

            updated = repository.update(
                session,
                account_id,
                AccountUpdate(password="new-password", cookies="new-cookie"),
            )
            assert updated is not None
            assert updated.secret.password_encrypted is not None
            assert updated.secret.cookies_encrypted is not None
            assert (
                SecretBox(TEST_KEY).decrypt(
                    updated.secret.password_encrypted,
                    purpose=f"account:{account_id}:password",
                )
                == "new-password"
            )
            assert (
                SecretBox(TEST_KEY).decrypt(
                    updated.secret.cookies_encrypted,
                    purpose=f"account:{account_id}:cookies",
                )
                == "new-cookie"
            )

            cleared = repository.update(
                session,
                account_id,
                AccountUpdate(clear_password=True, clear_cookies=True),
            )
            assert cleared is not None
            assert cleared.secret.password_encrypted is None
            assert cleared.secret.cookies_encrypted is None
            session.commit()
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_repository_rejects_conflicting_username_fingerprint() -> None:
    temp_dir, engine, repository = make_repository_database()
    try:
        with Session(engine) as session:
            first = repository.create(session, NewAccount(username="first-user"))
            second = repository.create(session, NewAccount(username="second-user"))
            session.commit()

            with pytest.raises(DuplicateAccountError, match="account already exists"):
                repository.update(
                    session,
                    second.id,
                    AccountUpdate(username=" FIRST-USER "),
                )
            session.rollback()

            persisted_second = session.get(Account, second.id)
            assert persisted_second is not None
            assert repository.reveal_username(persisted_second) == "second-user"
            assert repository.reveal_username(first) == "first-user"
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_repository_delete_removes_full_account_graph() -> None:
    temp_dir, engine, repository = make_repository_database()
    try:
        with Session(engine) as session:
            account = repository.create(session, NewAccount(username="delete-me"))
            task = StudyTask(
                account_id=account.id,
                course_id="course-1",
                class_id="class-1",
                cpi="10001",
                course_title="Course 1",
            )
            session.add(task)
            session.flush()
            session.add_all(
                [
                    TaskChapter(
                        task_id=task.id,
                        chapter_id="chapter-1",
                        chapter_title="Chapter 1",
                        position=0,
                    ),
                    TaskRun(
                        task_id=task.id,
                        worker_id="worker-1",
                        fencing_token=1,
                    ),
                    AccountLease(
                        account_id=account.id,
                        task_id=task.id,
                        owner_id="worker-1",
                        fencing_token=1,
                        expires_at=datetime.now(UTC) + timedelta(minutes=1),
                    ),
                    Event(
                        account_id=account.id,
                        task_id=task.id,
                        chapter_id="chapter-1",
                        kind="chapter.started",
                    ),
                ]
            )
            session.commit()

            assert repository.delete(session, account.id) is True
            session.commit()
            assert repository.delete(session, account.id) is False

            for model in (
                Account,
                AccountSecret,
                StudyTask,
                TaskChapter,
                TaskRun,
                AccountLease,
                Event,
            ):
                assert list(session.scalars(select(model))) == []
    finally:
        engine.dispose()
        temp_dir.cleanup()
