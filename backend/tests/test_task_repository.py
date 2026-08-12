from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import TaskStatus
from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import Account, StudyTask
from chaoxing_app.infrastructure.db.tasks import (
    DuplicateActiveTaskError,
    NewStudyTask,
    StudyTaskRepository,
)


def _new_task(account_id: int, *, course_id: str = "course-1") -> NewStudyTask:
    return NewStudyTask(
        account_id=account_id,
        course_id=course_id,
        class_id="class-1",
        cpi="cpi-1",
        course_title="Course",
        priority=0,
        run_after=datetime(2026, 8, 12, tzinfo=UTC),
        chapters=(),
        config_snapshot={},
    )


def _task_database(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}")
    create_schema(engine)
    with Session(engine) as session:
        account = Account(
            username_hint="user",
            username_fingerprint="task-repository-user",
        )
        session.add(account)
        session.commit()
        return engine, account.id


def test_partial_unique_index_only_rejects_duplicate_active_course(tmp_path) -> None:
    engine, account_id = _task_database(tmp_path)
    with Session(engine) as session:
        session.add(
            StudyTask(
                account_id=account_id,
                course_id="course-1",
                class_id="class-1",
                cpi="cpi-1",
                course_title="Active",
            )
        )
        session.commit()

        session.add(
            StudyTask(
                account_id=account_id,
                course_id="course-1",
                class_id="class-1",
                cpi="cpi-1",
                course_title="Duplicate active",
            )
        )
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            session.flush()
        session.rollback()

        session.add_all(
            [
                StudyTask(
                    account_id=account_id,
                    course_id="course-1",
                    class_id="class-1",
                    cpi="cpi-1",
                    course_title="Completed history",
                    status=TaskStatus.SUCCEEDED.value,
                ),
                StudyTask(
                    account_id=account_id,
                    course_id="course-2",
                    class_id="class-1",
                    cpi="cpi-1",
                    course_title="Different active course",
                ),
            ]
        )
        session.commit()
        assert session.scalar(select(func.count()).select_from(StudyTask)) == 3

    engine.dispose()


def test_repository_maps_unique_race_and_keeps_outer_transaction_usable(tmp_path) -> None:
    engine, account_id = _task_database(tmp_path)
    repository = StudyTaskRepository()
    with Session(engine) as session:
        repository.create(session, _new_task(account_id))
        session.commit()

    with Session(engine) as session:
        # Simulate a stale duplicate pre-check: the database constraint must still
        # produce the same domain error exposed by the API as HTTP 409.
        with (
            patch.object(session, "scalar", return_value=None),
            pytest.raises(DuplicateActiveTaskError, match="active task already exists"),
        ):
            repository.create(session, _new_task(account_id))

        created = repository.create(session, _new_task(account_id, course_id="course-2"))
        session.commit()
        assert created.course_id == "course-2"
        assert session.scalar(select(func.count()).select_from(StudyTask)) == 2

    engine.dispose()
