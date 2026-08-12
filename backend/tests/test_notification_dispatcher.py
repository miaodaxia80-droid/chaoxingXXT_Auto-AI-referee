from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.domain.integrations import NotificationChannelKind
from chaoxing_app.domain.tasks import TaskStatus
from chaoxing_app.infrastructure.db.engine import (
    create_database_engine,
    create_schema,
    make_session_factory,
)
from chaoxing_app.infrastructure.db.integrations import IntegrationSettingRepository
from chaoxing_app.infrastructure.db.models import (
    Account,
    Event,
    NotificationOutbox,
    StudyTask,
    utc_now,
)
from chaoxing_app.infrastructure.db.task_queue import claim_next_task, complete_task_run
from chaoxing_app.infrastructure.notification_dispatcher import (
    NotificationDispatcher,
    NotificationDispatcherConfig,
)
from chaoxing_app.infrastructure.notifications import NotificationResult
from chaoxing_app.infrastructure.security.secrets import SecretBox


class RecordingSender:
    sent: ClassVar[list[str]] = []
    fail = False

    def send(self, message) -> NotificationResult:
        if self.fail:
            raise RuntimeError("webhook-token=must-not-leak")
        self.__class__.sent.append(message.title)
        return NotificationResult(
            channel=NotificationChannelKind.BARK,
            delivered=True,
            status_code=200,
        )


def make_database() -> tuple[tempfile.TemporaryDirectory[str], object, SecretBox, str]:
    temp_dir: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
    engine = create_database_engine(
        f"sqlite:///{(Path(temp_dir.name) / 'notifications.db').as_posix()}"
    )
    create_schema(engine)
    secret_box = SecretBox(b"n" * 32)
    repository = IntegrationSettingRepository(secret_box=secret_box)
    with Session(engine) as session, session.begin():
        configured = repository.update_notification(
            session,
            NotificationChannelKind.BARK,
            enabled=True,
            secret_changes={"webhook_url": "https://notify.example.test/hook"},
        )
        account = Account(
            username_hint="notify-user",
            username_fingerprint="notify-fingerprint",
        )
        session.add(account)
        session.flush()
        task = StudyTask(
            account_id=account.id,
            course_id="course",
            class_id="class",
            cpi="cpi",
            course_title="Notification course",
            run_after=utc_now() - timedelta(minutes=1),
            config_snapshot={
                "notifications": [
                    {
                        "channel": "bark",
                        "enabled": True,
                        "config_revision": configured.revision,
                    }
                ]
            },
        )
        session.add(task)
        session.flush()
        task_id = task.id
    return temp_dir, engine, secret_box, task_id


def test_terminal_completion_enqueues_and_dispatches_notification() -> None:
    temp_dir, engine, secret_box, task_id = make_database()
    try:
        claim = claim_next_task(engine, owner_id="notification-test")
        assert claim is not None
        assert complete_task_run(engine, claim, status=TaskStatus.SUCCEEDED)
        with Session(engine) as session:
            outbox = session.scalar(select(NotificationOutbox))
            assert outbox is not None
            assert outbox.task_id == task_id
            assert outbox.status == "pending"

        RecordingSender.sent.clear()
        dispatcher = NotificationDispatcher(
            engine=engine,
            session_factory=make_session_factory(engine),
            secret_box=secret_box,
            owner_id="notification-dispatcher-test",
            sender_builder=lambda _configuration, _session: RecordingSender(),
        )
        assert dispatcher.dispatch_pending() == 1
        assert RecordingSender.sent == ["Chaoxing task succeeded"]
        with Session(engine) as session:
            outbox = session.scalar(select(NotificationOutbox))
            assert outbox is not None
            assert outbox.status == "sent"
            events = list(session.scalars(select(Event).where(Event.task_id == task_id)))
            assert any(event.kind == "notification.sent" for event in events)
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_notification_failure_is_retried_without_changing_task_result() -> None:
    temp_dir, engine, secret_box, task_id = make_database()
    try:
        claim = claim_next_task(engine, owner_id="notification-test")
        assert claim is not None
        assert complete_task_run(
            engine,
            claim,
            status=TaskStatus.FAILED,
            exit_reason="safe_failure",
        )
        RecordingSender.fail = True
        dispatcher = NotificationDispatcher(
            engine=engine,
            session_factory=make_session_factory(engine),
            secret_box=secret_box,
            owner_id="notification-dispatcher-test",
            config=NotificationDispatcherConfig(retry_delay=timedelta(seconds=1)),
            sender_builder=lambda _configuration, _session: RecordingSender(),
        )
        assert dispatcher.dispatch_pending() == 1
        with Session(engine) as session:
            task = session.get(StudyTask, task_id)
            assert task is not None
            assert task.status == TaskStatus.FAILED.value
            outbox = session.scalar(select(NotificationOutbox))
            assert outbox is not None
            assert outbox.status == "failed"
            assert outbox.last_error == "sender_failure"
            events = list(session.scalars(select(Event).where(Event.task_id == task_id)))
            serialized = repr([(event.kind, event.payload) for event in events])
            assert "must-not-leak" not in serialized
            assert "notification.failed" in serialized
    finally:
        RecordingSender.fail = False
        engine.dispose()
        temp_dir.cleanup()


def test_changed_notification_revision_is_skipped_without_sending() -> None:
    temp_dir, engine, secret_box, task_id = make_database()
    try:
        claim = claim_next_task(engine, owner_id="notification-test")
        assert claim is not None
        assert complete_task_run(engine, claim, status=TaskStatus.SUCCEEDED)
        with Session(engine) as session, session.begin():
            repository = IntegrationSettingRepository(secret_box=secret_box)
            repository.update_notification(
                session,
                NotificationChannelKind.BARK,
                enabled=True,
                secret_changes={
                    "webhook_url": "https://notify.example.test/changed-hook"
                },
            )

        RecordingSender.sent.clear()
        dispatcher = NotificationDispatcher(
            engine=engine,
            session_factory=make_session_factory(engine),
            secret_box=secret_box,
            owner_id="notification-dispatcher-test",
            sender_builder=lambda _configuration, _session: RecordingSender(),
        )
        assert dispatcher.dispatch_pending() == 1
        assert RecordingSender.sent == []
        with Session(engine) as session:
            task = session.get(StudyTask, task_id)
            outbox = session.scalar(select(NotificationOutbox))
            assert task is not None
            assert outbox is not None
            assert task.status == TaskStatus.SUCCEEDED.value
            assert outbox.status == "skipped"
            assert outbox.last_error == "configuration_revision_mismatch"
    finally:
        engine.dispose()
        temp_dir.cleanup()
