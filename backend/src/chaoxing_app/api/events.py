import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.api.dependencies import (
    AuthContext,
    get_db,
    get_session_factory,
    require_admin_csrf,
    require_auth,
)
from chaoxing_app.api.event_schemas import EventLevel, EventResponse, to_event_responses
from chaoxing_app.api.ownership import scoped_user_id
from chaoxing_app.infrastructure.db.events import (
    archive_events_before,
    archived_event_count,
    list_events,
)

router = APIRouter(prefix="/events", tags=["events"])


class EventArchiveRequest(BaseModel):
    before: datetime
    limit: int = Field(default=1000, strict=True, ge=1, le=10_000)


class EventArchiveResponse(BaseModel):
    archived: int
    archived_total: int


@router.post("/archive", response_model=EventArchiveResponse)
def archive_events(
    payload: EventArchiveRequest,
    _context: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> EventArchiveResponse:
    before = payload.before
    before = before.replace(tzinfo=UTC) if before.tzinfo is None else before.astimezone(UTC)
    if before > datetime.now(UTC) + timedelta(minutes=1):
        raise HTTPException(status_code=422, detail="archive cutoff cannot be in the future")
    count = archive_events_before(db, before=before, limit=payload.limit)
    return EventArchiveResponse(archived=count, archived_total=archived_event_count(db))


@router.get("", response_model=list[EventResponse])
def get_events(
    context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    after_id: int = Query(default=0, ge=0),
    account_id: int | None = Query(default=None, gt=0),
    task_id: str | None = Query(default=None, min_length=1, max_length=36),
    level: EventLevel | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[EventResponse]:
    events = list_events(
        db,
        after_id=after_id,
        account_id=account_id,
        task_id=task_id,
        level=level,
        user_id=scoped_user_id(context),
        limit=limit,
    )
    return to_event_responses(db, events)


def _format_sse(event: EventResponse) -> str:
    payload = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.id}\nevent: {event.kind}\ndata: {payload}\n\n"


async def _stream_events(
    request: Request,
    factory: sessionmaker[Session],
    *,
    after_id: int,
    follow: bool,
    account_id: int | None,
    task_id: str | None,
    level: EventLevel | None,
    user_id: int | None,
) -> AsyncIterator[str]:
    cursor = after_id
    heartbeat_deadline = asyncio.get_running_loop().time() + 15
    while True:
        with factory() as session:
            events = list_events(
                session,
                after_id=cursor,
                account_id=account_id,
                task_id=task_id,
                level=level,
                user_id=user_id,
                limit=200,
                ascending=True,
            )
            responses = to_event_responses(session, events)
            for event, response in zip(events, responses, strict=True):
                cursor = event.id
                yield _format_sse(response)
        if not follow or await request.is_disconnected():
            return
        now = asyncio.get_running_loop().time()
        if now >= heartbeat_deadline:
            yield ": keepalive\n\n"
            heartbeat_deadline = now + 15
        await asyncio.sleep(0.75)


@router.get("/stream", response_class=StreamingResponse)
def stream_events(
    request: Request,
    context: AuthContext = Depends(require_auth),
    factory: sessionmaker[Session] = Depends(get_session_factory),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
    after_id: int = Query(default=0, ge=0),
    account_id: int | None = Query(default=None, gt=0),
    task_id: str | None = Query(default=None, min_length=1, max_length=36),
    level: EventLevel | None = Query(default=None),
    follow: bool = Query(default=True),
) -> StreamingResponse:
    cursor = max(after_id, last_event_id or 0)
    return StreamingResponse(
        _stream_events(
            request,
            factory,
            after_id=cursor,
            follow=follow,
            account_id=account_id,
            task_id=task_id,
            level=level,
            user_id=scoped_user_id(context),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
