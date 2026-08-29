from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import Account, Event, StudyTask


def append_event(
    session: Session,
    *,
    kind: str,
    task_id: str | None = None,
    account_id: int | None = None,
    chapter_id: str | None = None,
    level: str = "info",
    payload: dict[str, Any] | None = None,
) -> Event:
    event = Event(
        task_id=task_id,
        account_id=account_id,
        chapter_id=chapter_id,
        kind=kind,
        level=level,
        payload=payload or {},
    )
    session.add(event)
    session.flush()
    return event


def list_task_events(
    session: Session,
    *,
    task_id: str,
    after_id: int = 0,
    limit: int = 200,
) -> list[Event]:
    statement = (
        select(Event)
        .where(Event.task_id == task_id, Event.id > after_id, Event.archived_at.is_(None))
        .order_by(Event.id)
        .limit(limit)
    )
    return list(session.scalars(statement))


def list_events(
    session: Session,
    *,
    after_id: int = 0,
    account_id: int | None = None,
    task_id: str | None = None,
    level: str | None = None,
    user_id: int | None = None,
    limit: int = 100,
    ascending: bool = False,
) -> list[Event]:
    statement = select(Event).where(Event.id > after_id, Event.archived_at.is_(None))
    if account_id is not None:
        statement = statement.where(Event.account_id == account_id)
    if task_id is not None:
        statement = statement.where(Event.task_id == task_id)
    if level is not None:
        statement = statement.where(Event.level == level)
    if user_id is not None:
        owned_account_ids = select(Account.id).where(Account.user_id == user_id)
        owned_task_ids = select(StudyTask.id).where(StudyTask.account_id.in_(owned_account_ids))
        statement = statement.where(
            or_(
                Event.account_id.in_(owned_account_ids),
                Event.task_id.in_(owned_task_ids),
            )
        )
    statement = statement.order_by(Event.id if ascending else Event.id.desc()).limit(limit)
    return list(session.scalars(statement))


def archive_events_before(
    session: Session,
    *,
    before: datetime,
    archived_at: datetime | None = None,
    limit: int = 1000,
) -> int:
    if limit < 1:
        raise ValueError("archive limit must be positive")
    event_ids = list(
        session.scalars(
            select(Event.id)
            .where(Event.archived_at.is_(None), Event.occurred_at < before)
            .order_by(Event.id)
            .limit(limit)
        )
    )
    if not event_ids:
        return 0
    result = cast(
        CursorResult[Any],
        session.execute(
            update(Event)
            .where(Event.id.in_(event_ids), Event.archived_at.is_(None))
            .values(archived_at=archived_at or datetime.now(UTC))
        ),
    )
    return int(result.rowcount or 0)


def purge_archived_events_before(
    session: Session,
    *,
    before: datetime,
    limit: int = 1000,
) -> int:
    if limit < 1:
        raise ValueError("purge limit must be positive")
    event_ids = list(
        session.scalars(
            select(Event.id)
            .where(Event.archived_at.is_not(None), Event.archived_at < before)
            .order_by(Event.id)
            .limit(limit)
        )
    )
    if not event_ids:
        return 0
    result = cast(
        CursorResult[Any],
        session.execute(delete(Event).where(Event.id.in_(event_ids))),
    )
    return int(result.rowcount or 0)


def archived_event_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(Event).where(Event.archived_at.is_not(None))
        )
        or 0
    )


def maintain_event_retention(
    session: Session,
    *,
    retention_days: int,
    now: datetime | None = None,
    limit: int = 1000,
) -> tuple[int, int]:
    if not 1 <= retention_days <= 3650:
        raise ValueError("event retention must be between 1 and 3650 days")
    current = now or datetime.now(UTC)
    archived = archive_events_before(
        session,
        before=current - timedelta(days=retention_days),
        archived_at=current,
        limit=limit,
    )
    purged = purge_archived_events_before(
        session,
        before=current - timedelta(days=retention_days),
        limit=limit,
    )
    return archived, purged
