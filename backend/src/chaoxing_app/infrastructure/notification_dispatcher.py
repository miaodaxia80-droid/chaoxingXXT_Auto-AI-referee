from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4

import requests
from sqlalchemy import select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.domain.integrations import NotificationChannelKind
from chaoxing_app.domain.tasks import TaskStatus, is_terminal_task
from chaoxing_app.infrastructure.db.integrations import (
    IntegrationConfigurationError,
    IntegrationSettingRepository,
    NotificationIntegrationRevisionMismatch,
    NotificationRuntimeConfiguration,
)
from chaoxing_app.infrastructure.db.models import Event, NotificationOutbox, StudyTask, utc_now
from chaoxing_app.infrastructure.notifications import (
    BarkNotificationSender,
    NotificationConfigurationError,
    NotificationError,
    NotificationMessage,
    NotificationSender,
    QmsgNotificationSender,
    ServerChanNotificationSender,
    TelegramNotificationSender,
)
from chaoxing_app.infrastructure.security.secrets import SecretBox

logger = logging.getLogger(__name__)

_PENDING = "pending"
_SENDING = "sending"
_SENT = "sent"
_SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class NotificationDispatcherConfig:
    batch_size: int = 8
    retry_delay: timedelta = timedelta(seconds=30)
    lock_timeout: timedelta = timedelta(minutes=5)
    max_attempts: int = 5

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("notification batch size must be positive")
        if self.retry_delay <= timedelta(0):
            raise ValueError("notification retry delay must be positive")
        if self.lock_timeout <= timedelta(0):
            raise ValueError("notification lock timeout must be positive")
        if self.max_attempts < 1:
            raise ValueError("notification max attempts must be positive")


@dataclass(frozen=True, slots=True)
class _OutboxClaim:
    id: str
    task_id: str
    channel: NotificationChannelKind
    config_revision: int
    attempts: int


class NotificationSenderBuilder(Protocol):
    def __call__(
        self,
        configuration: NotificationRuntimeConfiguration,
        session: requests.Session,
    ) -> NotificationSender: ...


def _build_sender(
    configuration: NotificationRuntimeConfiguration,
    session: requests.Session,
) -> NotificationSender:
    if configuration.channel is NotificationChannelKind.SERVER_CHAN:
        return ServerChanNotificationSender(
            webhook_url=configuration.webhook_url or "",
            session=session,
        )
    if configuration.channel is NotificationChannelKind.QMSG:
        return QmsgNotificationSender(
            webhook_url=configuration.webhook_url or "",
            session=session,
        )
    if configuration.channel is NotificationChannelKind.BARK:
        return BarkNotificationSender(
            webhook_url=configuration.webhook_url or "",
            session=session,
        )
    return TelegramNotificationSender(
        bot_token=configuration.bot_token or "",
        chat_id=configuration.chat_id or "",
        session=session,
    )


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


def _claim_pending(
    engine: Engine,
    *,
    owner_id: str,
    config: NotificationDispatcherConfig,
    now: datetime,
) -> tuple[_OutboxClaim, ...]:
    with _immediate_transaction(engine) as connection:
        stale_before = now - config.lock_timeout
        connection.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.status == _SENDING,
                NotificationOutbox.locked_at < stale_before,
            )
            .values(status=_PENDING, locked_at=None, locked_by=None)
        )
        rows = connection.execute(
            select(
                NotificationOutbox.id,
                NotificationOutbox.task_id,
                NotificationOutbox.channel,
                NotificationOutbox.config_revision,
                NotificationOutbox.attempts,
            )
            .where(
                NotificationOutbox.status.in_([_PENDING, "failed"]),
                NotificationOutbox.available_at <= now,
                NotificationOutbox.attempts < config.max_attempts,
            )
            .order_by(NotificationOutbox.available_at, NotificationOutbox.id)
            .limit(config.batch_size)
        ).all()
        claims: list[_OutboxClaim] = []
        for row in rows:
            try:
                channel = NotificationChannelKind(row.channel)
            except ValueError:
                connection.execute(
                    update(NotificationOutbox)
                    .where(NotificationOutbox.id == row.id)
                    .values(status=_SKIPPED, last_error="unsupported_channel")
                )
                continue
            result = connection.execute(
                update(NotificationOutbox)
                .where(
                    NotificationOutbox.id == row.id,
                    NotificationOutbox.status.in_([_PENDING, "failed"]),
                )
                .values(status=_SENDING, locked_at=now, locked_by=owner_id)
            )
            if result.rowcount == 1:
                claims.append(
                    _OutboxClaim(
                        id=row.id,
                        task_id=row.task_id,
                        channel=channel,
                        config_revision=row.config_revision,
                        attempts=row.attempts,
                    )
                )
        return tuple(claims)


def _safe_failure_reason(error: Exception) -> str:
    if isinstance(error, NotificationConfigurationError):
        return "configuration_invalid"
    if isinstance(error, NotificationError):
        return type(error).__name__.removesuffix("Error").casefold()
    return "sender_failure"


class NotificationDispatcher:
    """At-least-once sender for terminal task notification outbox rows."""

    def __init__(
        self,
        *,
        engine: Engine,
        session_factory: sessionmaker[Session],
        secret_box: SecretBox,
        config: NotificationDispatcherConfig | None = None,
        sender_builder: NotificationSenderBuilder = _build_sender,
        owner_id: str | None = None,
        provider_session_factory: Callable[[], requests.Session] = requests.Session,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self._repository = IntegrationSettingRepository(secret_box=secret_box)
        self._config = config or NotificationDispatcherConfig()
        self._sender_builder = sender_builder
        self._owner_id = owner_id or f"notification-dispatcher-{uuid4()}"
        self._provider_session_factory = provider_session_factory

    def dispatch_pending(self, *, now: datetime | None = None) -> int:
        dispatched_at = now or utc_now()
        claims = _claim_pending(
            self._engine,
            owner_id=self._owner_id,
            config=self._config,
            now=dispatched_at,
        )
        for claim in claims:
            self._dispatch_one(claim, dispatched_at)
        return len(claims)

    def _dispatch_one(self, claim: _OutboxClaim, now: datetime) -> None:
        try:
            with self._session_factory() as db:
                task = db.get(StudyTask, claim.task_id)
                if task is None or not is_terminal_task(TaskStatus(task.status)):
                    self._mark_skipped(claim, "task_not_terminal", now)
                    return
                configuration = self._repository.notification_runtime_configuration(
                    db,
                    claim.channel,
                    expected_revision=claim.config_revision,
                )
                if configuration is None:
                    self._mark_skipped(claim, "configuration_disabled", now)
                    return
                message = NotificationMessage(
                    title=f"Chaoxing task {task.status}",
                    body=(
                        f"Course: {task.course_title}\n"
                        f"Task ID: {task.id}\n"
                        f"Status: {task.status}"
                    ),
                )
            provider_session = self._provider_session_factory()
            try:
                sender = self._sender_builder(configuration, provider_session)
                result = sender.send(message)
            finally:
                provider_session.close()
            if not result.accepted:
                self._mark_failed(claim, "provider_rejected", now)
                return
        except NotificationIntegrationRevisionMismatch:
            self._mark_skipped(claim, "configuration_revision_mismatch", now)
            return
        except (IntegrationConfigurationError, NotificationConfigurationError):
            self._mark_skipped(claim, "configuration_invalid", now)
            return
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            self._mark_failed(claim, reason, now)
            logger.warning("notification delivery failed: %s", reason)
            return
        self._mark_sent(claim, now)

    def _mark_sent(self, claim: _OutboxClaim, now: datetime) -> None:
        with self._session_factory() as db, db.begin():
            row = db.get(NotificationOutbox, claim.id)
            if row is None or row.status != _SENDING or row.locked_by != self._owner_id:
                return
            row.status = _SENT
            row.sent_at = now
            row.locked_at = None
            row.locked_by = None
            row.last_error = None
            db.add(
                Event(
                    task_id=row.task_id,
                    kind="notification.sent",
                    level="info",
                    payload={"channel": row.channel, "outbox_id": row.id},
                    occurred_at=now,
                )
            )

    def _mark_skipped(self, claim: _OutboxClaim, reason: str, now: datetime) -> None:
        self._finish_claim(claim, _SKIPPED, reason, now, level="warning")

    def _mark_failed(self, claim: _OutboxClaim, reason: str, now: datetime) -> None:
        attempts = claim.attempts + 1
        if attempts >= self._config.max_attempts:
            self._finish_claim(claim, _SKIPPED, "max_attempts_exceeded", now, level="error")
            return
        with self._session_factory() as db, db.begin():
            row = db.get(NotificationOutbox, claim.id)
            if row is None or row.status != _SENDING or row.locked_by != self._owner_id:
                return
            row.status = "failed"
            row.attempts = attempts
            row.available_at = now + self._config.retry_delay * (2 ** min(attempts - 1, 5))
            row.locked_at = None
            row.locked_by = None
            row.last_error = reason
            db.add(
                Event(
                    task_id=row.task_id,
                    kind="notification.failed",
                    level="warning",
                    payload={"channel": row.channel, "outbox_id": row.id, "reason": reason},
                    occurred_at=now,
                )
            )

    def _finish_claim(
        self,
        claim: _OutboxClaim,
        status: str,
        reason: str,
        now: datetime,
        *,
        level: str,
    ) -> None:
        with self._session_factory() as db, db.begin():
            row = db.get(NotificationOutbox, claim.id)
            if row is None or row.status != _SENDING or row.locked_by != self._owner_id:
                return
            row.status = status
            row.locked_at = None
            row.locked_by = None
            row.last_error = reason
            db.add(
                Event(
                    task_id=row.task_id,
                    kind="notification.failed",
                    level=level,
                    payload={"channel": row.channel, "outbox_id": row.id, "reason": reason},
                    occurred_at=now,
                )
            )
