from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.api.dependencies import (
    AuthContext,
    get_db,
    get_secret_box,
    get_session_factory,
    require_auth,
    require_csrf,
)
from chaoxing_app.api.event_schemas import EventResponse, to_event_responses
from chaoxing_app.api.task_schemas import (
    BulkStudyTaskCreateRequest,
    BulkTaskActionItemResult,
    BulkTaskActionRequest,
    BulkTaskActionResponse,
    BulkTaskCreateItemResult,
    BulkTaskCreateResponse,
    BulkTaskDeleteItemResult,
    BulkTaskDeleteRequest,
    BulkTaskDeleteResponse,
    StudyTaskCreateRequest,
    StudyTaskDetailResponse,
    StudyTaskResponse,
    TaskChapterResponse,
    TaskHistoryCleanupResponse,
)
from chaoxing_app.domain.answer_profiles import (
    AnswerProfile,
    AnswerProfileError,
    merge_answer_profile,
    parse_answer_profile,
    validate_answer_profile_provider,
)
from chaoxing_app.domain.integrations import AnswerProviderKind
from chaoxing_app.domain.tasks import ChapterStatus, TaskStatus
from chaoxing_app.infrastructure.db.events import list_task_events
from chaoxing_app.infrastructure.db.integrations import (
    IntegrationConfigurationError,
    IntegrationSettingRepository,
)
from chaoxing_app.infrastructure.db.models import Account, StudyTask
from chaoxing_app.infrastructure.db.task_commands import (
    TaskCommandConflictError,
    TaskCommandNotFoundError,
    TaskCommandService,
)
from chaoxing_app.infrastructure.db.tasks import (
    ActiveTaskDeletionError,
    DuplicateActiveTaskError,
    NewStudyTask,
    NewTaskChapter,
    StudyTaskRepository,
    TaskDeletionNotFoundError,
    public_account_snapshot,
)
from chaoxing_app.infrastructure.security.secrets import SecretBox

router = APIRouter(prefix="/tasks", tags=["tasks"])

_SUCCESSFUL_CHAPTERS = {
    ChapterStatus.SUCCEEDED.value,
    ChapterStatus.ALREADY_COMPLETED.value,
}
_ATTENTION_CHAPTERS = {
    ChapterStatus.UNSUBMITTED.value,
    ChapterStatus.SKIPPED_NOT_OPEN.value,
    ChapterStatus.FAILED.value,
}


def _account_label(task: StudyTask) -> str:
    return task.account.remark or task.account.username_hint


def to_task_response(task: StudyTask) -> StudyTaskResponse:
    statuses = [chapter.status for chapter in task.chapters]
    return StudyTaskResponse(
        id=task.id,
        account_id=task.account_id,
        account_label=_account_label(task),
        course_id=task.course_id,
        class_id=task.class_id,
        cpi=task.cpi,
        course_title=task.course_title,
        status=task.status,
        desired_state=task.desired_state,
        priority=task.priority,
        selected_chapter_ids=task.selected_chapter_ids,
        chapter_total=len(statuses),
        chapter_succeeded=sum(value in _SUCCESSFUL_CHAPTERS for value in statuses),
        chapter_needs_attention=sum(value in _ATTENTION_CHAPTERS for value in statuses),
        created_at=task.created_at,
        updated_at=task.updated_at,
        run_after=task.run_after,
        started_at=task.started_at,
        finished_at=task.finished_at,
        last_error=task.last_error,
    )


def to_task_detail_response(task: StudyTask) -> StudyTaskDetailResponse:
    summary = to_task_response(task)
    return StudyTaskDetailResponse(
        **summary.model_dump(),
        chapters=[
            TaskChapterResponse(
                chapter_id=chapter.chapter_id,
                title=chapter.chapter_title,
                position=chapter.position,
                status=chapter.status,
                attempts=chapter.attempts,
                last_error=chapter.last_error,
                started_at=chapter.started_at,
                finished_at=chapter.finished_at,
            )
            for chapter in task.chapters
        ],
    )


def _create_task(
    payload: StudyTaskCreateRequest,
    db: Session,
    secret_box: SecretBox,
) -> StudyTask:
    account = db.get(Account, payload.account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    if not account.enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="account is disabled")

    repository = StudyTaskRepository()
    try:
        config_snapshot = public_account_snapshot(account)
        config_snapshot.update(
            IntegrationSettingRepository(secret_box=secret_box).answer_task_snapshot(db)
        )
        raw_answer = config_snapshot.get("answer")
        if isinstance(raw_answer, dict):
            raw_profile = raw_answer.get("profile")
            global_profile = (
                parse_answer_profile(raw_profile)
                if isinstance(raw_profile, dict)
                else AnswerProfile()
            )
            effective_profile = merge_answer_profile(
                global_profile,
                account.answer_profile_override,
            )
            if raw_answer.get("enabled") is True:
                raw_provider = raw_answer.get("provider")
                try:
                    if not isinstance(raw_provider, str):
                        raise ValueError
                    provider = AnswerProviderKind(raw_provider)
                    validate_answer_profile_provider(effective_profile, provider)
                except (AnswerProfileError, ValueError):
                    raise IntegrationConfigurationError(
                        "account answer profile is incompatible with the selected provider"
                    ) from None
            if effective_profile != AnswerProfile() or account.answer_profile_override:
                raw_answer["profile"] = effective_profile.to_json()
                raw_answer["profile_source"] = (
                    "account" if account.answer_profile_override else "global"
                )
        task = repository.create(
            db,
            NewStudyTask(
                account_id=account.id,
                course_id=payload.course_id,
                class_id=payload.class_id,
                cpi=payload.cpi,
                course_title=payload.course_title,
                priority=payload.priority,
                run_after=payload.run_after or datetime.now(UTC),
                chapters=tuple(
                    NewTaskChapter(
                        chapter_id=chapter.chapter_id,
                        title=chapter.title,
                        position=chapter.position,
                    )
                    for chapter in payload.chapters
                ),
                config_snapshot=config_snapshot,
            ),
        )
    except IntegrationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except DuplicateActiveTaskError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return task


@router.post("", response_model=StudyTaskDetailResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: StudyTaskCreateRequest,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
) -> StudyTaskDetailResponse:
    return to_task_detail_response(_create_task(payload, db, secret_box))


def _create_error_code(exc: HTTPException) -> str:
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        return "account_not_found"
    if exc.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
        return "invalid_integration"
    if exc.detail == "account is disabled":
        return "account_disabled"
    return "duplicate_active_task"


@router.post("/bulk-create", response_model=BulkTaskCreateResponse)
def bulk_create_tasks(
    payload: BulkStudyTaskCreateRequest,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
) -> BulkTaskCreateResponse:
    results: list[BulkTaskCreateItemResult] = []
    for item in payload.tasks:
        try:
            with db.begin_nested():
                task = _create_task(item, db, secret_box)
        except HTTPException as exc:
            results.append(
                BulkTaskCreateItemResult(
                    course_id=item.course_id,
                    class_id=item.class_id,
                    status="failed",
                    error_code=_create_error_code(exc),
                    error=str(exc.detail),
                )
            )
        else:
            results.append(
                BulkTaskCreateItemResult(
                    course_id=item.course_id,
                    class_id=item.class_id,
                    status="created",
                    task=to_task_detail_response(task),
                )
            )
    created = sum(result.status == "created" for result in results)
    return BulkTaskCreateResponse(
        total=len(results),
        created=created,
        failed=len(results) - created,
        results=results,
    )


@router.get("", response_model=list[StudyTaskResponse])
def list_tasks(
    _context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    account_id: int | None = Query(default=None, gt=0),
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> list[StudyTaskResponse]:
    tasks = StudyTaskRepository().list(
        db,
        account_id=account_id,
        status=task_status,
        limit=limit,
        offset=offset,
    )
    return [to_task_response(task) for task in tasks]


@router.get("/{task_id}", response_model=StudyTaskDetailResponse)
def get_task(
    task_id: str,
    _context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> StudyTaskDetailResponse:
    task = StudyTaskRepository().get(db, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return to_task_detail_response(task)


def _run_task_command(
    command: str,
    task_id: str,
    db: Session,
) -> StudyTaskDetailResponse:
    service = TaskCommandService()
    try:
        task = getattr(service, command)(db, task_id)
    except TaskCommandNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskCommandConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return to_task_detail_response(task)


@router.post("/bulk-action", response_model=BulkTaskActionResponse)
def bulk_task_action(
    payload: BulkTaskActionRequest,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> BulkTaskActionResponse:
    service = TaskCommandService()
    results: list[BulkTaskActionItemResult] = []
    for task_id in payload.task_ids:
        try:
            with db.begin_nested():
                task = getattr(service, payload.action)(db, task_id)
        except TaskCommandNotFoundError as exc:
            results.append(
                BulkTaskActionItemResult(
                    task_id=task_id,
                    status="failed",
                    error_code="task_not_found",
                    error=str(exc),
                )
            )
        except TaskCommandConflictError as exc:
            results.append(
                BulkTaskActionItemResult(
                    task_id=task_id,
                    status="failed",
                    error_code="invalid_state",
                    error=str(exc),
                )
            )
        else:
            results.append(
                BulkTaskActionItemResult(
                    task_id=task_id,
                    status="succeeded",
                    task=to_task_detail_response(task),
                )
            )
    succeeded = sum(result.status == "succeeded" for result in results)
    return BulkTaskActionResponse(
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )


@router.delete("/bulk", response_model=BulkTaskDeleteResponse)
def bulk_delete_tasks(
    payload: BulkTaskDeleteRequest,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> BulkTaskDeleteResponse:
    repository = StudyTaskRepository()
    results: list[BulkTaskDeleteItemResult] = []
    for task_id in payload.task_ids:
        try:
            with db.begin_nested():
                repository.delete_terminal(db, task_id)
        except TaskDeletionNotFoundError as exc:
            results.append(
                BulkTaskDeleteItemResult(
                    task_id=task_id,
                    status="failed",
                    error_code="task_not_found",
                    error=str(exc),
                )
            )
        except ActiveTaskDeletionError as exc:
            results.append(
                BulkTaskDeleteItemResult(
                    task_id=task_id,
                    status="failed",
                    error_code="active_task",
                    error=str(exc),
                )
            )
        else:
            results.append(BulkTaskDeleteItemResult(task_id=task_id, status="deleted"))
    deleted = sum(result.status == "deleted" for result in results)
    return BulkTaskDeleteResponse(
        total=len(results),
        deleted=deleted,
        failed=len(results) - deleted,
        results=results,
    )


@router.delete("/history", response_model=TaskHistoryCleanupResponse)
def clear_terminal_task_history(
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> TaskHistoryCleanupResponse:
    deleted = StudyTaskRepository().delete_terminal_history(db)
    return TaskHistoryCleanupResponse(deleted=deleted)


@router.post("/{task_id}/pause", response_model=StudyTaskDetailResponse)
def pause_task(
    task_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> StudyTaskDetailResponse:
    return _run_task_command("pause", task_id, db)


@router.post("/{task_id}/resume", response_model=StudyTaskDetailResponse)
def resume_task(
    task_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> StudyTaskDetailResponse:
    return _run_task_command("resume", task_id, db)


@router.post("/{task_id}/cancel", response_model=StudyTaskDetailResponse)
def cancel_task(
    task_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> StudyTaskDetailResponse:
    return _run_task_command("cancel", task_id, db)


@router.get("/{task_id}/events", response_model=list[EventResponse])
def get_task_events(
    task_id: str,
    _context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[EventResponse]:
    if db.get(StudyTask, task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    events = list_task_events(db, task_id=task_id, after_id=after, limit=limit)
    return to_event_responses(db, events)


def _format_sse(event: EventResponse) -> str:
    payload = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.id}\nevent: {event.kind}\ndata: {payload}\n\n"


async def _stream_task_events(
    request: Request,
    factory: sessionmaker[Session],
    *,
    task_id: str,
    after_id: int,
    follow: bool,
) -> AsyncIterator[str]:
    cursor = after_id
    heartbeat_deadline = asyncio.get_running_loop().time() + 15
    while True:
        with factory() as session:
            events = list_task_events(session, task_id=task_id, after_id=cursor, limit=200)
            responses = to_event_responses(session, events)
            for event, response in zip(events, responses, strict=True):
                cursor = event.id
                yield _format_sse(response)

        if not follow:
            return
        if await request.is_disconnected():
            return
        now = asyncio.get_running_loop().time()
        if now >= heartbeat_deadline:
            yield ": keepalive\n\n"
            heartbeat_deadline = now + 15
        await asyncio.sleep(0.75)


@router.get("/{task_id}/events/stream", response_class=StreamingResponse)
def stream_task_events(
    task_id: str,
    request: Request,
    _context: AuthContext = Depends(require_auth),
    factory: sessionmaker[Session] = Depends(get_session_factory),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
    after: int = Query(default=0, ge=0),
    follow: bool = Query(default=True),
) -> StreamingResponse:
    with factory() as session:
        if session.get(StudyTask, task_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    cursor = max(after, last_event_id or 0)
    return StreamingResponse(
        _stream_task_events(
            request,
            factory,
            task_id=task_id,
            after_id=cursor,
            follow=follow,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
