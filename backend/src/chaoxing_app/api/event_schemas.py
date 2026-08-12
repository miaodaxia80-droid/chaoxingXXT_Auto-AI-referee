from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import Event, TaskChapter

EventLevel = Literal["info", "warning", "error"]

_PUBLIC_PAYLOAD_KEYS = frozenset(
    {
        "answered_count",
        "attempt",
        "channel",
        "count",
        "course_id",
        "course_title",
        "coverage",
        "duration",
        "exit_reason",
        "from",
        "kind",
        "lease_expires_at",
        "play_time",
        "provider_error_count",
        "provider_result_reason",
        "reason",
        "recovery_attempt",
        "remaining",
        "run_id",
        "selected_chapters",
        "status",
        "task_type",
        "total_questions",
        "to",
        "unopened_policy",
    }
)
_MAX_PUBLIC_STRING_LENGTH = 500


class EventResponse(BaseModel):
    id: int
    task_id: str | None
    account_id: int | None
    chapter_id: str | None
    chapter_title: str | None
    kind: str
    level: str
    payload: dict[str, Any]
    occurred_at: datetime


def public_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the small scalar subset of event metadata intended for operators."""
    public: dict[str, Any] = {}
    for key in _PUBLIC_PAYLOAD_KEYS:
        value = payload.get(key)
        if value is None or isinstance(value, bool | int | float):
            if key in payload:
                public[key] = value
        elif isinstance(value, str):
            public[key] = value[:_MAX_PUBLIC_STRING_LENGTH]
    return public


def to_event_response(event: Event, *, chapter_title: str | None = None) -> EventResponse:
    return EventResponse(
        id=event.id,
        task_id=event.task_id,
        account_id=event.account_id,
        chapter_id=event.chapter_id,
        chapter_title=chapter_title,
        kind=event.kind,
        level=event.level,
        payload=public_event_payload(event.payload),
        occurred_at=event.occurred_at,
    )


def to_event_responses(session: Session, events: Sequence[Event]) -> list[EventResponse]:
    """Serialize events and resolve their persisted chapter titles in one query."""
    keys = {
        (event.task_id, event.chapter_id)
        for event in events
        if event.task_id is not None and event.chapter_id is not None
    }
    if not keys:
        return [to_event_response(event) for event in events]

    task_ids = {task_id for task_id, _chapter_id in keys}
    chapter_ids = {chapter_id for _task_id, chapter_id in keys}
    rows = session.execute(
        select(TaskChapter.task_id, TaskChapter.chapter_id, TaskChapter.chapter_title).where(
            TaskChapter.task_id.in_(task_ids),
            TaskChapter.chapter_id.in_(chapter_ids),
        )
    )
    titles = {
        (row.task_id, row.chapter_id): row.chapter_title
        for row in rows
        if (row.task_id, row.chapter_id) in keys
    }
    return [
        to_event_response(
            event,
            chapter_title=titles.get((event.task_id, event.chapter_id)),
        )
        for event in events
    ]
