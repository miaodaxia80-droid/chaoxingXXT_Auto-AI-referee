from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self, TypeVar, cast

import pytest
import requests
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from chaoxing_app.domain.tasks import ChapterStatus, DesiredTaskState, TaskStatus
from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import (
    Account,
    AccountLease,
    Event,
    StudyTask,
    TaskChapter,
)
from chaoxing_app.infrastructure.db.task_queue import TaskClaim, claim_next_task
from chaoxing_app.platform.client import ChaoxingClient
from chaoxing_app.platform.errors import PlatformAuthenticationError, PlatformParseError
from chaoxing_app.platform.models import Chapter, Course, CourseOutline
from chaoxing_app.platform.task_points.cards import (
    ChapterTaskBundle,
    DocumentTaskPoint,
    JobDefaults,
    QuizTaskPoint,
    ReadTaskPoint,
    UnsupportedTaskPoint,
    VideoTaskPoint,
)
from chaoxing_app.platform.task_points.document import DocumentCompletionResult
from chaoxing_app.platform.task_points.empty_page import EmptyPageCompletionResult
from chaoxing_app.platform.task_points.quiz import (
    QuizSubmissionMode,
    QuizSubmissionResult,
    QuizSubmissionStatus,
)
from chaoxing_app.platform.task_points.reading import ReadingCompletionResult
from chaoxing_app.platform.task_points.video import MediaKind, MediaTask
from chaoxing_app.worker.control import (
    TaskCancelRequested,
    TaskLeaseLost,
    TaskPauseRequested,
    WorkerControl,
)
from chaoxing_app.worker.media_playback import PlaybackResult, ProgressCallback
from chaoxing_app.worker.study_executor import StudyTaskExecutor
from chaoxing_app.worker.task_progress import ClaimedTaskProgress

ResultT = TypeVar("ResultT")


@dataclass(slots=True)
class StubCourseClient:
    outline: CourseOutline
    error: Exception | None = None
    courses: list[Course] = field(default_factory=list)

    def get_course_outline(self, course: Course) -> CourseOutline:
        self.courses.append(course)
        if self.error is not None:
            raise self.error
        return self.outline


class StubAccountSession(AbstractContextManager["StubAccountSession"]):
    def __init__(self, client: StubCourseClient) -> None:
        self.session = requests.Session()
        self.client = client
        self.user_id = "user-42"
        self.fid = "fid-7"
        self.execute_count = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.session.close()

    def execute(self, operation: Callable[[ChaoxingClient], ResultT]) -> ResultT:
        self.execute_count += 1
        return operation(cast(ChaoxingClient, self.client))


@dataclass(slots=True)
class StubAccountRuntime:
    account: StubAccountSession
    opened_account_ids: list[int] = field(default_factory=list)

    def open(self, account_id: int) -> StubAccountSession:
        self.opened_account_ids.append(account_id)
        return self.account


@dataclass(slots=True)
class StubChapterClient:
    bundles: Mapping[str, ChapterTaskBundle]
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def fetch(self, _course: Course, chapter: Chapter) -> ChapterTaskBundle:
        self.calls.append(chapter.chapter_id)
        if self.error is not None:
            raise self.error
        return self.bundles[chapter.chapter_id]


@dataclass(slots=True)
class StubDocumentClient:
    calls: list[str] = field(default_factory=list)

    def complete(self, _course: Course, task: DocumentTaskPoint) -> DocumentCompletionResult:
        self.calls.append(task.job_id)
        return DocumentCompletionResult(True, 200, "knowledge-doc")


@dataclass(slots=True)
class StubReadingClient:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(
        self,
        _course: Course,
        task: ReadTaskPoint,
        *,
        knowledge_id: str,
    ) -> ReadingCompletionResult:
        self.calls.append((task.job_id, knowledge_id))
        return ReadingCompletionResult(True, 200, "ok")


@dataclass(slots=True)
class StubEmptyPageClient:
    calls: list[str] = field(default_factory=list)

    def complete(self, _course: Course, chapter: Chapter) -> EmptyPageCompletionResult:
        self.calls.append(chapter.chapter_id)
        return EmptyPageCompletionResult(True, 200, chapter.chapter_id)


def unsubmitted_quiz_result() -> QuizSubmissionResult:
    return QuizSubmissionResult(
        status=QuizSubmissionStatus.UNSUBMITTED,
        accepted=False,
        coverage=0.0,
        answered_count=0,
        total_questions=2,
        reason="provider_unconfigured",
    )


@dataclass(slots=True)
class StubQuizClient:
    result: QuizSubmissionResult = field(default_factory=unsubmitted_quiz_result)
    calls: list[dict[str, object]] = field(default_factory=list)

    def complete(
        self,
        course: Course,
        task: QuizTaskPoint,
        *,
        defaults: JobDefaults | None,
        provider: object | None,
        course_context: str,
        mode: QuizSubmissionMode,
        submit_threshold: float,
    ) -> QuizSubmissionResult:
        self.calls.append(
            {
                "course": course,
                "task": task,
                "defaults": defaults,
                "provider": provider,
                "course_context": course_context,
                "mode": mode,
                "submit_threshold": submit_threshold,
            }
        )
        return self.result


@dataclass(slots=True)
class StubPlayback:
    progress: ProgressCallback
    calls: list[dict[str, object]]

    def run(
        self,
        *,
        course: Course,
        task: MediaTask,
        fid: str,
        user_id: str,
        speed: float,
        kind: MediaKind,
        report_interval_seconds: int,
    ) -> PlaybackResult:
        self.calls.append(
            {
                "course": course,
                "task": task,
                "fid": fid,
                "user_id": user_id,
                "speed": speed,
                "kind": kind,
                "report_interval_seconds": report_interval_seconds,
            }
        )
        self.progress("video.started", {"duration": 10, "play_time": 0})
        return PlaybackResult(10, 10, 1)


@dataclass(slots=True)
class ExecutorHarness:
    chapter_client: StubChapterClient
    document_client: StubDocumentClient = field(default_factory=StubDocumentClient)
    reading_client: StubReadingClient = field(default_factory=StubReadingClient)
    empty_client: StubEmptyPageClient = field(default_factory=StubEmptyPageClient)
    quiz_client: StubQuizClient = field(default_factory=StubQuizClient)
    playback_calls: list[dict[str, object]] = field(default_factory=list)
    sessions: list[requests.Session] = field(default_factory=list)

    def executor(
        self,
        *,
        engine: Engine,
        runtime: StubAccountRuntime,
    ) -> StudyTaskExecutor:
        def remember(value: object, session: requests.Session) -> object:
            self.sessions.append(session)
            return value

        return StudyTaskExecutor(
            engine=engine,
            account_runtime=runtime,  # type: ignore[arg-type]
            chapter_client_factory=lambda session: cast(
                StubChapterClient, remember(self.chapter_client, session)
            ),
            document_client_factory=lambda session: cast(
                StubDocumentClient, remember(self.document_client, session)
            ),
            reading_client_factory=lambda session: cast(
                StubReadingClient, remember(self.reading_client, session)
            ),
            empty_page_client_factory=lambda session: cast(
                StubEmptyPageClient, remember(self.empty_client, session)
            ),
            quiz_client_factory=lambda session: cast(
                StubQuizClient, remember(self.quiz_client, session)
            ),
            video_client_factory=lambda session: cast(object, remember(object(), session)),  # type: ignore[arg-type]
            playback_factory=lambda _client, _control, progress: StubPlayback(
                progress,
                self.playback_calls,
            ),
        )


@dataclass(frozen=True, slots=True)
class TaskDatabase:
    engine: Engine
    account_id: int
    task_id: str
    claim: TaskClaim


def make_task_database(
    root: Path,
    chapter_statuses: list[tuple[str, ChapterStatus]],
    *,
    unopened_policy: str = "retry",
    speed: float = 1.75,
    chapter_concurrency: int | None = None,
) -> TaskDatabase:
    engine = create_database_engine(f"sqlite+pysqlite:///{root / 'executor.db'}")
    create_schema(engine)
    with Session(engine) as session, session.begin():
        account = Account(
            remark="executor account",
            username_hint="u***",
            username_fingerprint="executor-fingerprint",
        )
        session.add(account)
        session.flush()
        config_snapshot: dict[str, object] = {
            "speed": speed,
            "unopened_policy": unopened_policy,
        }
        if chapter_concurrency is not None:
            config_snapshot["chapter_concurrency"] = chapter_concurrency
        task = StudyTask(
            account_id=account.id,
            course_id="course-1",
            class_id="class-1",
            cpi="cpi-1",
            course_title="Course One",
            status=TaskStatus.QUEUED.value,
            config_snapshot=config_snapshot,
        )
        task.chapters = [
            TaskChapter(
                chapter_id=chapter_id,
                chapter_title=f"Chapter {position}",
                position=position,
                status=status.value,
            )
            for position, (chapter_id, status) in enumerate(chapter_statuses)
        ]
        session.add(task)
        session.flush()
        account_id = account.id
        task_id = task.id
    claim = claim_next_task(engine, owner_id="executor-test", lease_duration=timedelta(minutes=5))
    assert claim is not None
    return TaskDatabase(engine, account_id, task_id, claim)


def chapter(
    chapter_id: str,
    *,
    completed: bool = False,
    locked: bool = False,
    jobs: int = 1,
) -> Chapter:
    return Chapter(chapter_id, f"Outline {chapter_id}", jobs, completed, locked)


def bundle(*tasks: object, attachments: int | None = None) -> ChapterTaskBundle:
    typed_tasks = cast(tuple[object, ...], tasks)
    return ChapterTaskBundle(
        tasks=cast(object, typed_tasks),
        defaults=JobDefaults(report_time_interval=17, knowledge_id="knowledge-default"),
        not_open=False,
        attachment_count=len(tasks) if attachments is None else attachments,
        completed_attachment_count=0,
    )


def load_chapters(engine: Engine, task_id: str) -> dict[str, TaskChapter]:
    with Session(engine) as session:
        rows = session.scalars(select(TaskChapter).where(TaskChapter.task_id == task_id)).all()
        session.expunge_all()
    return {row.chapter_id: row for row in rows}


def load_events(engine: Engine, task_id: str) -> list[Event]:
    with Session(engine) as session:
        rows = list(
            session.scalars(select(Event).where(Event.task_id == task_id).order_by(Event.id))
        )
        session.expunge_all()
    return rows


def run_executor(
    database: TaskDatabase,
    outline: CourseOutline,
    harness: ExecutorHarness,
    *,
    course_error: Exception | None = None,
) -> tuple[TaskStatus, StubAccountSession]:
    account = StubAccountSession(StubCourseClient(outline, course_error))
    runtime = StubAccountRuntime(account)
    executor = harness.executor(engine=database.engine, runtime=runtime)
    status = executor.execute(
        database.claim,
        WorkerControl(engine=database.engine, claim=database.claim),
    )
    assert runtime.opened_account_ids == [database.account_id]
    return status, account


def test_executes_supported_points_in_one_authenticated_session(tmp_path: Path) -> None:
    database = make_task_database(
        tmp_path,
        [
            ("already", ChapterStatus.PENDING),
            ("points", ChapterStatus.PENDING),
            ("empty", ChapterStatus.PENDING),
        ],
    )
    video = VideoTaskPoint(MediaTask("video-job", "object-1", "info"), mid="mid")
    document = DocumentTaskPoint("doc-job", "object-2", "nodeId_points", "jtoken")
    reading = ReadTaskPoint("read-job", "item-1", "Read", "info", "jtoken")
    harness = ExecutorHarness(
        StubChapterClient(
            {
                "points": bundle(video, document, reading),
                "empty": bundle(attachments=0),
            }
        )
    )

    status, account = run_executor(
        database,
        CourseOutline(
            (
                chapter("already", completed=True),
                chapter("points", jobs=3),
                chapter("empty", jobs=0),
            )
        ),
        harness,
    )

    assert status is TaskStatus.SUCCEEDED
    rows = load_chapters(database.engine, database.task_id)
    assert rows["already"].status == ChapterStatus.ALREADY_COMPLETED.value
    assert rows["points"].status == ChapterStatus.SUCCEEDED.value
    assert rows["empty"].status == ChapterStatus.SUCCEEDED.value
    assert all(row.attempts == 1 for row in rows.values())
    assert harness.chapter_client.calls == ["points", "empty"]
    assert harness.document_client.calls == ["doc-job"]
    assert harness.reading_client.calls == [("read-job", "knowledge-default")]
    assert harness.empty_client.calls == ["empty"]
    assert len(harness.playback_calls) == 1
    assert harness.playback_calls[0]["speed"] == 1.75
    assert harness.playback_calls[0]["fid"] == "fid-7"
    assert harness.playback_calls[0]["user_id"] == "user-42"
    assert harness.playback_calls[0]["report_interval_seconds"] == 17
    assert harness.sessions and all(value is account.session for value in harness.sessions)
    kinds = [event.kind for event in load_events(database.engine, database.task_id)]
    assert "chapter.video.started" in kinds
    assert "chapter.task_point_completed" in kinds
    assert "chapter.document.completed" in kinds
    assert "chapter.reading.completed" in kinds
    assert "chapter.empty_page.completed" in kinds


def test_only_pending_and_running_chapters_are_retried(tmp_path: Path) -> None:
    database = make_task_database(
        tmp_path,
        [
            ("success", ChapterStatus.SUCCEEDED),
            ("failed", ChapterStatus.FAILED),
            ("pending", ChapterStatus.PENDING),
            ("running", ChapterStatus.RUNNING),
        ],
    )
    harness = ExecutorHarness(
        StubChapterClient(
            {
                "pending": ChapterTaskBundle((), None, False, 1, 1),
                "running": ChapterTaskBundle((), None, False, 1, 1),
            }
        )
    )

    status, _account = run_executor(
        database,
        CourseOutline(
            tuple(chapter(value) for value in ["success", "failed", "pending", "running"])
        ),
        harness,
    )

    assert status is TaskStatus.NEEDS_ATTENTION
    rows = load_chapters(database.engine, database.task_id)
    assert rows["success"].attempts == 0
    assert rows["failed"].attempts == 0
    assert rows["pending"].status == ChapterStatus.ALREADY_COMPLETED.value
    assert rows["running"].status == ChapterStatus.ALREADY_COMPLETED.value
    assert rows["pending"].attempts == 1
    assert rows["running"].attempts == 1
    assert harness.chapter_client.calls == ["pending", "running"]


def test_quiz_and_unsupported_points_need_attention_without_guessing(tmp_path: Path) -> None:
    database = make_task_database(
        tmp_path,
        [("quiz", ChapterStatus.PENDING), ("unknown", ChapterStatus.PENDING)],
    )
    quiz = QuizTaskPoint("quiz-job", "quiz-info")
    unsupported = UnsupportedTaskPoint("unknown-job", "live", "remote secret detail")
    harness = ExecutorHarness(
        StubChapterClient({"quiz": bundle(quiz), "unknown": bundle(unsupported)})
    )

    status, _account = run_executor(
        database,
        CourseOutline((chapter("quiz"), chapter("unknown"))),
        harness,
    )

    assert status is TaskStatus.NEEDS_ATTENTION
    rows = load_chapters(database.engine, database.task_id)
    assert rows["quiz"].status == ChapterStatus.UNSUBMITTED.value
    assert rows["quiz"].last_error == "quiz_requires_answers"
    assert rows["unknown"].status == ChapterStatus.FAILED.value
    assert rows["unknown"].last_error == "unsupported_task_point"
    serialized = repr(
        [(event.kind, event.payload) for event in load_events(database.engine, database.task_id)]
    )
    assert "remote secret detail" not in serialized
    assert not harness.document_client.calls
    assert not harness.reading_client.calls
    assert not harness.playback_calls
    assert harness.quiz_client.calls[0]["mode"] is QuizSubmissionMode.AUTO
    assert harness.quiz_client.calls[0]["submit_threshold"] == 0.8
    serialized_events = repr(
        [(event.kind, event.payload) for event in load_events(database.engine, database.task_id)]
    )
    assert "chapter.quiz.unsubmitted" in serialized_events
    assert "provider_unconfigured" in serialized_events


def test_submitted_quiz_completes_without_exposing_answers(tmp_path: Path) -> None:
    database = make_task_database(tmp_path, [("quiz", ChapterStatus.PENDING)])
    quiz_client = StubQuizClient(
        QuizSubmissionResult(
            status=QuizSubmissionStatus.SUBMITTED,
            accepted=True,
            coverage=1.0,
            answered_count=2,
            total_questions=2,
            status_code=200,
        )
    )
    harness = ExecutorHarness(
        StubChapterClient({"quiz": bundle(QuizTaskPoint("quiz-job", "quiz-info"))}),
        quiz_client=quiz_client,
    )

    status, _account = run_executor(
        database,
        CourseOutline((chapter("quiz"),)),
        harness,
    )

    assert status is TaskStatus.SUCCEEDED
    row = load_chapters(database.engine, database.task_id)["quiz"]
    assert row.status == ChapterStatus.SUCCEEDED.value
    events = load_events(database.engine, database.task_id)
    submitted = next(event for event in events if event.kind == "chapter.quiz.submitted")
    assert submitted.payload["coverage"] == 1.0
    assert all(
        event.payload["run_id"] == database.claim.run_id
        for event in events
        if event.kind.startswith("chapter.")
    )
    assert "answer" not in submitted.payload
    assert "answers" not in submitted.payload


@pytest.mark.parametrize("policy", ["retry", "skip"])
def test_not_open_is_terminal_without_creating_duplicate_tasks(
    tmp_path: Path,
    policy: str,
) -> None:
    database = make_task_database(
        tmp_path,
        [("locked", ChapterStatus.PENDING), ("server-locked", ChapterStatus.PENDING)],
        unopened_policy=policy,
    )
    harness = ExecutorHarness(
        StubChapterClient(
            {
                "server-locked": ChapterTaskBundle((), None, True, 0, 0),
            }
        )
    )

    status, _account = run_executor(
        database,
        CourseOutline((chapter("locked", locked=True), chapter("server-locked"))),
        harness,
    )

    assert status is TaskStatus.NEEDS_ATTENTION
    rows = load_chapters(database.engine, database.task_id)
    assert {row.status for row in rows.values()} == {ChapterStatus.SKIPPED_NOT_OPEN.value}
    events = [
        event
        for event in load_events(database.engine, database.task_id)
        if event.kind == "chapter.skipped_not_open"
    ]
    assert len(events) == 2
    assert all(event.payload["unopened_policy"] == policy for event in events)
    with Session(database.engine) as session:
        assert session.scalar(select(func.count()).select_from(StudyTask)) == 1


def test_platform_failures_are_persisted_without_exception_text(tmp_path: Path) -> None:
    database = make_task_database(
        tmp_path,
        [("one", ChapterStatus.PENDING), ("two", ChapterStatus.PENDING)],
    )
    secret = "password=hunter2 cookie=_uid-secret"
    harness = ExecutorHarness(StubChapterClient({}, PlatformParseError(secret, secret)))

    status, _account = run_executor(
        database,
        CourseOutline((chapter("one"), chapter("two"))),
        harness,
    )

    assert status is TaskStatus.NEEDS_ATTENTION
    rows = load_chapters(database.engine, database.task_id)
    assert {row.last_error for row in rows.values()} == {"platform_response_invalid"}
    persisted = repr(
        [
            (event.kind, event.level, event.payload)
            for event in load_events(database.engine, database.task_id)
        ]
    )
    assert secret not in persisted
    assert "hunter2" not in persisted


def test_outline_authentication_failure_is_fatal_and_leaves_chapters_runnable(
    tmp_path: Path,
) -> None:
    database = make_task_database(
        tmp_path,
        [("one", ChapterStatus.PENDING), ("two", ChapterStatus.RUNNING)],
    )
    harness = ExecutorHarness(StubChapterClient({}))

    with pytest.raises(PlatformAuthenticationError):
        run_executor(
            database,
            CourseOutline(()),
            harness,
            course_error=PlatformAuthenticationError("username and password rejected"),
        )

    rows = load_chapters(database.engine, database.task_id)
    assert rows["one"].status == ChapterStatus.PENDING.value
    assert rows["two"].status == ChapterStatus.RUNNING.value
    assert not [
        event
        for event in load_events(database.engine, database.task_id)
        if event.kind.startswith("chapter.")
    ]


def test_chapter_authentication_failure_is_fatal_and_stops_later_chapters(
    tmp_path: Path,
) -> None:
    database = make_task_database(
        tmp_path,
        [("one", ChapterStatus.PENDING), ("two", ChapterStatus.PENDING)],
    )
    harness = ExecutorHarness(
        StubChapterClient({}, PlatformAuthenticationError("cookie=must-not-leak"))
    )

    with pytest.raises(PlatformAuthenticationError):
        run_executor(
            database,
            CourseOutline((chapter("one"), chapter("two"))),
            harness,
        )

    rows = load_chapters(database.engine, database.task_id)
    assert rows["one"].status == ChapterStatus.FAILED.value
    assert rows["one"].last_error == "platform_authentication_failed"
    assert rows["two"].status == ChapterStatus.PENDING.value
    assert "must-not-leak" not in repr(load_events(database.engine, database.task_id))


def test_unresolved_incomplete_attachments_need_attention(tmp_path: Path) -> None:
    database = make_task_database(tmp_path, [("one", ChapterStatus.PENDING)])
    harness = ExecutorHarness(StubChapterClient({"one": ChapterTaskBundle((), None, False, 2, 1)}))

    status, _account = run_executor(
        database,
        CourseOutline((chapter("one"),)),
        harness,
    )

    assert status is TaskStatus.NEEDS_ATTENTION
    row = load_chapters(database.engine, database.task_id)["one"]
    assert row.status == ChapterStatus.FAILED.value
    assert row.last_error == "unresolved_task_points"


def test_missing_card_payload_with_declared_job_cannot_be_marked_empty(tmp_path: Path) -> None:
    database = make_task_database(tmp_path, [("one", ChapterStatus.PENDING)])
    harness = ExecutorHarness(
        StubChapterClient({"one": ChapterTaskBundle((), None, False, 0, 0)})
    )

    status, _account = run_executor(
        database,
        CourseOutline((chapter("one", jobs=1),)),
        harness,
    )

    assert status is TaskStatus.NEEDS_ATTENTION
    row = load_chapters(database.engine, database.task_id)["one"]
    assert row.status == ChapterStatus.FAILED.value
    assert row.last_error == "unresolved_task_points"
    assert harness.empty_client.calls == []


def test_mixed_known_and_unresolved_attachments_cannot_succeed(tmp_path: Path) -> None:
    database = make_task_database(tmp_path, [("one", ChapterStatus.PENDING)])
    quiz = QuizTaskPoint("quiz-job", "quiz-info")
    harness = ExecutorHarness(
        StubChapterClient(
            {
                "one": ChapterTaskBundle(
                    (quiz,),
                    None,
                    False,
                    2,
                    0,
                    1,
                )
            }
        )
    )

    status, _account = run_executor(
        database,
        CourseOutline((chapter("one"),)),
        harness,
    )

    assert status is TaskStatus.NEEDS_ATTENTION
    row = load_chapters(database.engine, database.task_id)["one"]
    assert row.status == ChapterStatus.FAILED.value
    assert row.last_error == "unsupported_task_point"


def test_unexpected_chapter_error_is_sanitized_then_reraised(tmp_path: Path) -> None:
    database = make_task_database(
        tmp_path,
        [("one", ChapterStatus.PENDING), ("two", ChapterStatus.PENDING)],
    )
    harness = ExecutorHarness(StubChapterClient({}, RuntimeError("api-key=secret")))

    with pytest.raises(RuntimeError, match="api-key=secret"):
        run_executor(
            database,
            CourseOutline((chapter("one"), chapter("two"))),
            harness,
        )

    rows = load_chapters(database.engine, database.task_id)
    assert rows["one"].status == ChapterStatus.FAILED.value
    assert rows["one"].last_error == "internal_execution_error"
    assert rows["two"].status == ChapterStatus.PENDING.value
    assert "api-key=secret" not in repr(load_events(database.engine, database.task_id))


def test_stale_claim_cannot_write_chapter_or_event(tmp_path: Path) -> None:
    database = make_task_database(tmp_path, [("one", ChapterStatus.PENDING)])
    with Session(database.engine) as session, session.begin():
        session.execute(
            update(AccountLease)
            .where(AccountLease.account_id == database.account_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    progress = ClaimedTaskProgress(engine=database.engine, claim=database.claim)
    with pytest.raises(TaskLeaseLost):
        progress.start_chapter("one")

    rows = load_chapters(database.engine, database.task_id)
    assert rows["one"].status == ChapterStatus.PENDING.value
    assert rows["one"].attempts == 0
    assert not [
        event
        for event in load_events(database.engine, database.task_id)
        if event.kind.startswith("chapter.")
    ]


def test_event_write_rechecks_fencing_after_chapter_start(tmp_path: Path) -> None:
    database = make_task_database(tmp_path, [("one", ChapterStatus.PENDING)])
    progress = ClaimedTaskProgress(engine=database.engine, claim=database.claim)
    progress.start_chapter("one")
    with Session(database.engine) as session, session.begin():
        session.execute(
            update(Account)
            .where(Account.id == database.account_id)
            .values(lease_version=Account.lease_version + 1)
        )

    with pytest.raises(TaskLeaseLost):
        progress.record_event("one", kind="chapter.test", payload={"value": 1})

    assert "chapter.test" not in {
        event.kind for event in load_events(database.engine, database.task_id)
    }


def test_cancel_at_initial_checkpoint_marks_runnable_chapters_canceled(tmp_path: Path) -> None:
    database = make_task_database(
        tmp_path,
        [("pending", ChapterStatus.PENDING), ("running", ChapterStatus.RUNNING)],
    )
    with Session(database.engine) as session, session.begin():
        session.execute(
            update(StudyTask)
            .where(StudyTask.id == database.task_id)
            .values(
                status=TaskStatus.CANCEL_REQUESTED.value,
                desired_state=DesiredTaskState.CANCEL.value,
            )
        )
    harness = ExecutorHarness(StubChapterClient({}))

    with pytest.raises(TaskCancelRequested):
        run_executor(database, CourseOutline(()), harness)
    rows = load_chapters(database.engine, database.task_id)
    assert {row.status for row in rows.values()} == {ChapterStatus.CANCELED.value}


class TrackingAccountSession(StubAccountSession):
    def __init__(self, client: StubCourseClient) -> None:
        super().__init__(client)
        self.closed = False

    def __exit__(self, *_args: object) -> None:
        self.closed = True
        super().__exit__(*_args)


@dataclass(slots=True)
class FreshAccountRuntime:
    outline: CourseOutline
    accounts: list[TrackingAccountSession] = field(default_factory=list)
    opened_account_ids: list[int] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def open(self, account_id: int) -> TrackingAccountSession:
        account = TrackingAccountSession(StubCourseClient(self.outline))
        with self.lock:
            self.accounts.append(account)
            self.opened_account_ids.append(account_id)
        return account


@dataclass(slots=True)
class ConcurrentChapterState:
    bundles: Mapping[str, ChapterTaskBundle]
    errors: Mapping[str, Exception] = field(default_factory=dict)
    delay_seconds: float = 0.05
    calls: list[str] = field(default_factory=list)
    sessions: list[requests.Session] = field(default_factory=list)
    active: int = 0
    max_active: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def make_client(self, session: requests.Session) -> ConcurrentChapterClient:
        with self.lock:
            self.sessions.append(session)
        return ConcurrentChapterClient(state=self)

    def fetch(self, chapter: Chapter) -> ChapterTaskBundle:
        with self.lock:
            self.calls.append(chapter.chapter_id)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay_seconds)
            error = self.errors.get(chapter.chapter_id)
            if error is not None:
                raise error
            return self.bundles[chapter.chapter_id]
        finally:
            with self.lock:
                self.active -= 1


@dataclass(slots=True)
class ConcurrentChapterClient:
    state: ConcurrentChapterState

    def fetch(self, _course: Course, chapter: Chapter) -> ChapterTaskBundle:
        return self.state.fetch(chapter)


@dataclass(slots=True)
class ControlBarrierState:
    engine: Engine
    task_id: str
    status: TaskStatus
    desired_state: DesiredTaskState
    calls: list[str] = field(default_factory=list)
    sessions: list[requests.Session] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    barrier: threading.Barrier = field(init=False)

    def __post_init__(self) -> None:
        self.barrier = threading.Barrier(2, action=self.request_control)

    def request_control(self) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(
                update(StudyTask)
                .where(StudyTask.id == self.task_id)
                .values(status=self.status.value, desired_state=self.desired_state.value)
            )

    def make_client(self, session: requests.Session) -> ControlBarrierClient:
        with self.lock:
            self.sessions.append(session)
        return ControlBarrierClient(state=self)


@dataclass(slots=True)
class ControlBarrierClient:
    state: ControlBarrierState

    def fetch(self, _course: Course, chapter: Chapter) -> ChapterTaskBundle:
        with self.state.lock:
            self.state.calls.append(chapter.chapter_id)
        self.state.barrier.wait(timeout=3)
        return ChapterTaskBundle((), None, False, 1, 1)


def run_concurrent_executor(
    database: TaskDatabase,
    outline: CourseOutline,
    *,
    chapter_client_factory: Callable[[requests.Session], object],
) -> tuple[TaskStatus, FreshAccountRuntime]:
    runtime = FreshAccountRuntime(outline)
    executor = StudyTaskExecutor(
        engine=database.engine,
        account_runtime=runtime,  # type: ignore[arg-type]
        chapter_client_factory=chapter_client_factory,  # type: ignore[arg-type]
    )
    status = executor.execute(
        database.claim,
        WorkerControl(engine=database.engine, claim=database.claim),
    )
    return status, runtime


def test_concurrent_chapters_use_bounded_isolated_account_sessions(tmp_path: Path) -> None:
    chapter_ids = ["one", "two", "three", "four"]
    database = make_task_database(
        tmp_path,
        [(chapter_id, ChapterStatus.PENDING) for chapter_id in chapter_ids],
        chapter_concurrency=2,
    )
    completed_bundle = ChapterTaskBundle((), None, False, 1, 1)
    state = ConcurrentChapterState(
        bundles={chapter_id: completed_bundle for chapter_id in chapter_ids}
    )

    status, runtime = run_concurrent_executor(
        database,
        CourseOutline(tuple(chapter(chapter_id) for chapter_id in chapter_ids)),
        chapter_client_factory=state.make_client,
    )

    assert status is TaskStatus.SUCCEEDED
    assert state.max_active == 2
    assert set(state.calls) == set(chapter_ids)
    assert len(state.sessions) == len(chapter_ids)
    assert len({id(session) for session in state.sessions}) == len(chapter_ids)
    assert len(runtime.accounts) == len(chapter_ids) + 1  # outline plus one per chapter
    assert all(account.closed for account in runtime.accounts)
    assert set(state.sessions) == {account.session for account in runtime.accounts[1:]}


def test_concurrent_chapter_failure_does_not_stop_other_chapters(tmp_path: Path) -> None:
    chapter_ids = ["one", "bad", "three"]
    database = make_task_database(
        tmp_path,
        [(chapter_id, ChapterStatus.PENDING) for chapter_id in chapter_ids],
        chapter_concurrency=2,
    )
    completed_bundle = ChapterTaskBundle((), None, False, 1, 1)
    state = ConcurrentChapterState(
        bundles={chapter_id: completed_bundle for chapter_id in chapter_ids},
        errors={"bad": RuntimeError("api-key=must-not-leak")},
    )

    with pytest.raises(RuntimeError, match="api-key=must-not-leak"):
        run_concurrent_executor(
            database,
            CourseOutline(tuple(chapter(chapter_id) for chapter_id in chapter_ids)),
            chapter_client_factory=state.make_client,
        )

    rows = load_chapters(database.engine, database.task_id)
    assert rows["bad"].status == ChapterStatus.FAILED.value
    assert rows["bad"].last_error == "internal_execution_error"
    assert rows["one"].status == ChapterStatus.ALREADY_COMPLETED.value
    assert rows["three"].status == ChapterStatus.ALREADY_COMPLETED.value
    assert set(state.calls) == set(chapter_ids)
    assert "must-not-leak" not in repr(load_events(database.engine, database.task_id))


@pytest.mark.parametrize(
    ("requested_status", "desired_state", "exception_type"),
    [
        (TaskStatus.PAUSE_REQUESTED, DesiredTaskState.PAUSE, TaskPauseRequested),
        (TaskStatus.CANCEL_REQUESTED, DesiredTaskState.CANCEL, TaskCancelRequested),
    ],
)
def test_concurrent_control_request_stops_submitting_new_chapters(
    tmp_path: Path,
    requested_status: TaskStatus,
    desired_state: DesiredTaskState,
    exception_type: type[Exception],
) -> None:
    chapter_ids = ["one", "two", "three", "four"]
    database = make_task_database(
        tmp_path,
        [(chapter_id, ChapterStatus.PENDING) for chapter_id in chapter_ids],
        chapter_concurrency=2,
    )
    outline = CourseOutline(tuple(chapter(chapter_id) for chapter_id in chapter_ids))
    runtime = FreshAccountRuntime(outline)
    state = ControlBarrierState(
        engine=database.engine,
        task_id=database.task_id,
        status=requested_status,
        desired_state=desired_state,
    )
    executor = StudyTaskExecutor(
        engine=database.engine,
        account_runtime=runtime,  # type: ignore[arg-type]
        chapter_client_factory=state.make_client,  # type: ignore[arg-type]
    )

    with pytest.raises(exception_type):
        executor.execute(
            database.claim,
            WorkerControl(engine=database.engine, claim=database.claim),
        )

    assert set(state.calls) == {"one", "two"}
    assert len(runtime.accounts) == 3  # outline plus the two started chapters
    assert all(account.closed for account in runtime.accounts)
    rows = load_chapters(database.engine, database.task_id)
    if desired_state is DesiredTaskState.CANCEL:
        assert {row.status for row in rows.values()} == {ChapterStatus.CANCELED.value}
    else:
        assert rows["one"].status == ChapterStatus.RUNNING.value
        assert rows["two"].status == ChapterStatus.RUNNING.value
        assert rows["three"].status == ChapterStatus.PENDING.value
        assert rows["four"].status == ChapterStatus.PENDING.value
