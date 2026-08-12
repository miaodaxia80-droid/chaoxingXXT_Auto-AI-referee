from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import AbstractContextManager, suppress
from typing import Protocol, TypeVar

import requests
from sqlalchemy import Engine

from chaoxing_app.application.account_runtime import AccountRuntimeError
from chaoxing_app.application.answer_runtime import (
    AnswerProviderBinding,
    AnswerProviderRuntimePort,
    DisabledAnswerProviderRuntime,
)
from chaoxing_app.domain.tasks import ChapterStatus, TaskStatus, summarize_chapters
from chaoxing_app.infrastructure.db.task_queue import TaskClaim
from chaoxing_app.platform.client import ChaoxingClient
from chaoxing_app.platform.errors import (
    PlatformAuthenticationError,
    PlatformError,
    PlatformParseError,
)
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
from chaoxing_app.platform.task_points.document import (
    DocumentCompletionResult,
    DocumentTaskClient,
)
from chaoxing_app.platform.task_points.empty_page import (
    EmptyPageCompletionResult,
    EmptyPageTaskClient,
)
from chaoxing_app.platform.task_points.quiz import (
    AnswerProvider,
    QuizSubmissionMode,
    QuizSubmissionResult,
    QuizSubmissionStatus,
    QuizTaskClient,
)
from chaoxing_app.platform.task_points.reading import (
    ReadingCompletionResult,
    ReadingTaskClient,
)
from chaoxing_app.platform.task_points.video import MediaKind, MediaTask, VideoProgressClient
from chaoxing_app.worker.control import (
    TaskCancelRequested,
    TaskLeaseLost,
    TaskPauseRequested,
    WorkerControl,
)
from chaoxing_app.worker.media_playback import (
    MediaPlaybackRunner,
    MediaProgressPort,
    PlaybackResult,
    ProgressCallback,
)
from chaoxing_app.worker.task_progress import (
    ClaimedTaskProgress,
    TaskChapterSnapshot,
    TaskExecutionSnapshot,
)

ResultT = TypeVar("ResultT")
AccountSessionT_co = TypeVar("AccountSessionT_co", bound="AccountStudySession", covariant=True)


class AccountStudySession(Protocol):
    @property
    def session(self) -> requests.Session: ...

    @property
    def user_id(self) -> str: ...

    @property
    def fid(self) -> str: ...

    def execute(self, operation: Callable[[ChaoxingClient], ResultT]) -> ResultT: ...


class AccountRuntimePort(Protocol[AccountSessionT_co]):
    def open(self, account_id: int) -> AbstractContextManager[AccountSessionT_co]: ...


class ChapterTaskPort(Protocol):
    def fetch(self, course: Course, chapter: Chapter) -> ChapterTaskBundle: ...


class DocumentTaskPort(Protocol):
    def complete(self, course: Course, task: DocumentTaskPoint) -> DocumentCompletionResult: ...


class ReadingTaskPort(Protocol):
    def complete(
        self,
        course: Course,
        task: ReadTaskPoint,
        *,
        knowledge_id: str,
    ) -> ReadingCompletionResult: ...


class EmptyPageTaskPort(Protocol):
    def complete(self, course: Course, chapter: Chapter) -> EmptyPageCompletionResult: ...


class QuizTaskPort(Protocol):
    def complete(
        self,
        course: Course,
        task: QuizTaskPoint,
        *,
        defaults: JobDefaults | None,
        provider: AnswerProvider | None,
        course_context: str,
        mode: QuizSubmissionMode,
        submit_threshold: float,
    ) -> QuizSubmissionResult: ...


class PlaybackPort(Protocol):
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
    ) -> PlaybackResult: ...


ChapterClientFactory = Callable[[requests.Session], ChapterTaskPort]
DocumentClientFactory = Callable[[requests.Session], DocumentTaskPort]
ReadingClientFactory = Callable[[requests.Session], ReadingTaskPort]
EmptyPageClientFactory = Callable[[requests.Session], EmptyPageTaskPort]
QuizClientFactory = Callable[[requests.Session], QuizTaskPort]
VideoClientFactory = Callable[[requests.Session], MediaProgressPort]
PlaybackFactory = Callable[[MediaProgressPort, WorkerControl, ProgressCallback], PlaybackPort]


def _chapter_client(session: requests.Session) -> ChapterTaskPort:
    from chaoxing_app.platform.task_points.cards import ChapterTaskClient

    return ChapterTaskClient(session=session)


def _document_client(session: requests.Session) -> DocumentTaskPort:
    return DocumentTaskClient(session=session)


def _reading_client(session: requests.Session) -> ReadingTaskPort:
    return ReadingTaskClient(session=session)


def _empty_page_client(session: requests.Session) -> EmptyPageTaskPort:
    return EmptyPageTaskClient(session=session)


def _quiz_client(session: requests.Session) -> QuizTaskPort:
    return QuizTaskClient(session=session)


def _video_client(session: requests.Session) -> MediaProgressPort:
    return VideoProgressClient(session=session)


def _playback_runner(
    client: MediaProgressPort,
    control: WorkerControl,
    progress: ProgressCallback,
) -> PlaybackPort:
    return MediaPlaybackRunner(client=client, control=control, progress=progress)


class TaskPointRejected(PlatformError):
    pass


class StudyTaskExecutor:
    def __init__(
        self,
        *,
        engine: Engine,
        account_runtime: AccountRuntimePort[AccountStudySession],
        chapter_client_factory: ChapterClientFactory = _chapter_client,
        document_client_factory: DocumentClientFactory = _document_client,
        reading_client_factory: ReadingClientFactory = _reading_client,
        empty_page_client_factory: EmptyPageClientFactory = _empty_page_client,
        quiz_client_factory: QuizClientFactory = _quiz_client,
        answer_provider_runtime: AnswerProviderRuntimePort | None = None,
        video_client_factory: VideoClientFactory = _video_client,
        playback_factory: PlaybackFactory = _playback_runner,
    ) -> None:
        self._engine = engine
        self._account_runtime = account_runtime
        self._chapter_client_factory = chapter_client_factory
        self._document_client_factory = document_client_factory
        self._reading_client_factory = reading_client_factory
        self._empty_page_client_factory = empty_page_client_factory
        self._quiz_client_factory = quiz_client_factory
        self._answer_provider_runtime = (
            answer_provider_runtime or DisabledAnswerProviderRuntime()
        )
        self._video_client_factory = video_client_factory
        self._playback_factory = playback_factory

    def execute(self, claim: TaskClaim, control: WorkerControl) -> TaskStatus:
        progress = ClaimedTaskProgress(engine=self._engine, claim=claim)
        snapshot = progress.load()
        try:
            control.checkpoint()
            course = self._course(snapshot)
            concurrency = self._chapter_concurrency(snapshot.config)
            if concurrency == 1:
                # Keep the single-session path for the default configuration.  Besides
                # avoiding needless re-authentication, this preserves the old execution
                # semantics for tasks created before chapter concurrency was introduced.
                with self._account_runtime.open(snapshot.account_id) as account:
                    outline = self._authenticated(
                        account,
                        lambda client: client.get_course_outline(course),
                        control,
                    )
                    self._run_chapters_serial(
                        snapshot=snapshot,
                        course=course,
                        outline=outline,
                        account=account,
                        control=control,
                        progress=progress,
                    )
            else:
                # The outline is read once, then each chapter gets an isolated runtime
                # and HTTP session.  Sharing a requests.Session across worker threads
                # would also make cookie refresh/CAS ordering nondeterministic.
                with self._account_runtime.open(snapshot.account_id) as account:
                    outline = self._authenticated(
                        account,
                        lambda client: client.get_course_outline(course),
                        control,
                    )
                self._run_chapters_concurrent(
                    claim=claim,
                    snapshot=snapshot,
                    course=course,
                    outline=outline,
                    control=control,
                    max_workers=concurrency,
                )
            control.checkpoint()
            return summarize_chapters(progress.statuses()).terminal_status
        except TaskCancelRequested:
            progress.cancel_runnable_chapters()
            raise
        except (TaskPauseRequested, TaskLeaseLost):
            raise

    def _run_chapters_serial(
        self,
        *,
        snapshot: TaskExecutionSnapshot,
        course: Course,
        outline: CourseOutline,
        account: AccountStudySession,
        control: WorkerControl,
        progress: ClaimedTaskProgress,
    ) -> None:
        chapters_by_id = {chapter.chapter_id: chapter for chapter in outline.chapters}
        for persisted in snapshot.chapters:
            if persisted.status not in {ChapterStatus.PENDING, ChapterStatus.RUNNING}:
                continue
            control.checkpoint()
            progress.start_chapter(persisted.chapter_id)
            chapter = chapters_by_id.get(persisted.chapter_id)
            if chapter is None:
                progress.finish_chapter(
                    persisted.chapter_id,
                    status=ChapterStatus.FAILED,
                    reason="chapter_not_found",
                )
                continue
            if chapter.is_completed:
                progress.finish_chapter(
                    persisted.chapter_id,
                    status=ChapterStatus.ALREADY_COMPLETED,
                )
                continue
            if chapter.requires_unlock:
                self._finish_not_open(snapshot, persisted.chapter_id, progress)
                continue
            self._run_chapter(
                snapshot=snapshot,
                course=course,
                chapter=chapter,
                account=account,
                control=control,
                progress=progress,
            )

    def _run_chapters_concurrent(
        self,
        *,
        claim: TaskClaim,
        snapshot: TaskExecutionSnapshot,
        course: Course,
        outline: CourseOutline,
        control: WorkerControl,
        max_workers: int,
    ) -> None:
        """Run runnable chapters with a bounded set of isolated account runtimes.

        Only ``max_workers`` futures are submitted at any point.  This matters for
        pause/cancel requests: chapters that have not started remain pending and can
        be left for a later run instead of opening an account runtime after the task
        has already been asked to stop.
        """

        chapters_by_id = {chapter.chapter_id: chapter for chapter in outline.chapters}
        runnable = tuple(
            persisted
            for persisted in snapshot.chapters
            if persisted.status in {ChapterStatus.PENDING, ChapterStatus.RUNNING}
        )
        if not runnable:
            return

        worker_count = min(max_workers, len(runnable))
        executor = ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="chaoxing-chapter",
        )
        futures: dict[Future[None], str] = {}
        next_index = 0
        control_error: TaskPauseRequested | TaskCancelRequested | TaskLeaseLost | None = None
        unexpected_error: Exception | None = None

        def submit_one(persisted_index: int) -> Future[None]:
            persisted = runnable[persisted_index]
            chapter = chapters_by_id.get(persisted.chapter_id)
            return executor.submit(
                self._run_concurrent_chapter,
                claim=claim,
                snapshot=snapshot,
                course=course,
                persisted=persisted,
                chapter=chapter,
                control=control,
            )

        try:
            # Fill the bounded queue.  Check before every submission so a request
            # arriving while scheduling does not start more work than necessary.
            while next_index < len(runnable) and len(futures) < worker_count:
                try:
                    control.checkpoint()
                except (TaskPauseRequested, TaskCancelRequested, TaskLeaseLost) as exc:
                    control_error = exc
                    break
                future = submit_one(next_index)
                futures[future] = runnable[next_index].chapter_id
                next_index += 1

            while futures:
                done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for future in done:
                    futures.pop(future, None)
                    try:
                        future.result()
                    except (TaskPauseRequested, TaskCancelRequested, TaskLeaseLost) as exc:
                        if control_error is None:
                            control_error = exc
                    except Exception as exc:
                        # A worker normally sanitizes and persists chapter failures
                        # itself.  Keep a defensive record for failures from the
                        # wrapper/progress layer and continue collecting other chapters.
                        if unexpected_error is None:
                            unexpected_error = exc

                if control_error is not None:
                    for future in futures:
                        future.cancel()
                    continue

                # Refill exactly the slots freed by this completion batch.
                for _ in range(len(done)):
                    if next_index >= len(runnable):
                        break
                    try:
                        control.checkpoint()
                    except (TaskPauseRequested, TaskCancelRequested, TaskLeaseLost) as exc:
                        control_error = exc
                        for future in futures:
                            future.cancel()
                        break
                    future = submit_one(next_index)
                    futures[future] = runnable[next_index].chapter_id
                    next_index += 1
        finally:
            # Running calls are allowed to observe WorkerControl and close their
            # account contexts.  Pending futures are cancelled by the executor.
            executor.shutdown(wait=True, cancel_futures=True)

        if control_error is not None:
            raise control_error
        if unexpected_error is not None:
            raise unexpected_error

    def _run_concurrent_chapter(
        self,
        *,
        claim: TaskClaim,
        snapshot: TaskExecutionSnapshot,
        course: Course,
        persisted: TaskChapterSnapshot,
        chapter: Chapter | None,
        control: WorkerControl,
    ) -> None:
        """Execute one chapter with a fresh progress writer and account runtime."""

        # The immutable snapshot avoids copying mutable ORM state into a thread.
        chapter_id = persisted.chapter_id
        chapter_progress = ClaimedTaskProgress(engine=self._engine, claim=claim)

        control.checkpoint()
        chapter_progress.start_chapter(chapter_id)
        try:
            if chapter is None:
                chapter_progress.finish_chapter(
                    chapter_id,
                    status=ChapterStatus.FAILED,
                    reason="chapter_not_found",
                )
                return
            if chapter.is_completed:
                chapter_progress.finish_chapter(
                    chapter_id,
                    status=ChapterStatus.ALREADY_COMPLETED,
                )
                return
            if chapter.requires_unlock:
                self._finish_not_open(snapshot, chapter_id, chapter_progress)
                return

            # Every chapter that needs platform work owns its own runtime/session.
            with self._account_runtime.open(snapshot.account_id) as account:
                control.checkpoint()
                self._run_chapter(
                    snapshot=snapshot,
                    course=course,
                    chapter=chapter,
                    account=account,
                    control=control,
                    progress=chapter_progress,
                    rethrow_unexpected=True,
                )
        except (TaskPauseRequested, TaskCancelRequested, TaskLeaseLost):
            raise
        except (AccountRuntimeError, PlatformError) as exc:
            # Runtime construction/authentication can fail before _run_chapter gets
            # control, so sanitize and persist that failure here.
            control.checkpoint()
            fatal = isinstance(exc, (AccountRuntimeError, PlatformAuthenticationError))
            try:
                chapter_progress.finish_chapter(
                    chapter_id,
                    status=ChapterStatus.FAILED,
                    reason=self._safe_failure_reason(exc),
                )
            except TaskLeaseLost:
                if not fatal:
                    raise
            if fatal:
                raise
        except Exception:
            control.checkpoint()
            with suppress(TaskLeaseLost):
                chapter_progress.finish_chapter(
                    chapter_id,
                    status=ChapterStatus.FAILED,
                    reason="internal_execution_error",
                )
            raise

    def _run_chapter(
        self,
        *,
        snapshot: TaskExecutionSnapshot,
        course: Course,
        chapter: Chapter,
        account: AccountStudySession,
        control: WorkerControl,
        progress: ClaimedTaskProgress,
        rethrow_unexpected: bool = True,
    ) -> None:
        try:
            chapter_client = self._chapter_client_factory(account.session)
            bundle = self._authenticated(
                account,
                lambda _client: chapter_client.fetch(course, chapter),
                control,
            )
            if bundle.not_open:
                self._finish_not_open(
                    snapshot,
                    chapter.chapter_id,
                    progress,
                )
                return
            if not bundle.tasks:
                self._finish_without_pending_points(
                    course=course,
                    chapter=chapter,
                    bundle=bundle,
                    account=account,
                    control=control,
                    progress=progress,
                )
                return
            if any(isinstance(task_point, QuizTaskPoint) for task_point in bundle.tasks):
                with self._answer_provider_runtime.open(snapshot.config) as binding:
                    attention = self._run_task_points(
                        snapshot=snapshot,
                        course=course,
                        chapter=chapter,
                        bundle=bundle,
                        account=account,
                        control=control,
                        progress=progress,
                        answer_binding=binding,
                    )
            else:
                attention = self._run_task_points(
                    snapshot=snapshot,
                    course=course,
                    chapter=chapter,
                    bundle=bundle,
                    account=account,
                    control=control,
                    progress=progress,
                    answer_binding=None,
                )
            progress.finish_chapter(
                chapter.chapter_id,
                status=attention or ChapterStatus.SUCCEEDED,
                reason=self._attention_reason(attention),
            )
        except (TaskPauseRequested, TaskCancelRequested, TaskLeaseLost):
            raise
        except (AccountRuntimeError, PlatformError) as exc:
            control.checkpoint()
            progress.finish_chapter(
                chapter.chapter_id,
                status=ChapterStatus.FAILED,
                reason=self._safe_failure_reason(exc),
            )
            if isinstance(exc, (AccountRuntimeError, PlatformAuthenticationError)):
                raise
        except Exception:
            control.checkpoint()
            progress.finish_chapter(
                chapter.chapter_id,
                status=ChapterStatus.FAILED,
                reason="internal_execution_error",
            )
            if rethrow_unexpected:
                raise

    def _finish_without_pending_points(
        self,
        *,
        course: Course,
        chapter: Chapter,
        bundle: ChapterTaskBundle,
        account: AccountStudySession,
        control: WorkerControl,
        progress: ClaimedTaskProgress,
    ) -> None:
        if bundle.attachment_count and (
            bundle.completed_attachment_count >= bundle.attachment_count
        ):
            progress.finish_chapter(
                chapter.chapter_id,
                status=ChapterStatus.ALREADY_COMPLETED,
            )
            return
        if bundle.attachment_count:
            progress.finish_chapter(
                chapter.chapter_id,
                status=ChapterStatus.FAILED,
                reason="unresolved_task_points",
            )
            return
        if chapter.job_count > 0:
            progress.finish_chapter(
                chapter.chapter_id,
                status=ChapterStatus.FAILED,
                reason="unresolved_task_points",
            )
            return
        client = self._empty_page_client_factory(account.session)
        result = self._authenticated(
            account,
            lambda _active: client.complete(course, chapter),
            control,
        )
        if not result.accepted:
            raise TaskPointRejected
        self._record_point_completed(progress, chapter.chapter_id, "empty_page")
        progress.finish_chapter(chapter.chapter_id, status=ChapterStatus.SUCCEEDED)

    def _run_task_points(
        self,
        *,
        snapshot: TaskExecutionSnapshot,
        course: Course,
        chapter: Chapter,
        bundle: ChapterTaskBundle,
        account: AccountStudySession,
        control: WorkerControl,
        progress: ClaimedTaskProgress,
        answer_binding: AnswerProviderBinding | None,
    ) -> ChapterStatus | None:
        attention: ChapterStatus | None = None
        if bundle.unresolved_attachment_count:
            progress.record_event(
                chapter.chapter_id,
                kind="chapter.unresolved_task_points",
                level="warning",
                payload={
                    "task_type": "unresolved",
                    "count": bundle.unresolved_attachment_count,
                },
            )
            attention = ChapterStatus.FAILED
        for task_point in bundle.tasks:
            control.checkpoint()
            if isinstance(task_point, QuizTaskPoint):
                binding = answer_binding or AnswerProviderBinding()
                quiz_attention = self._run_quiz(
                    snapshot=snapshot,
                    course=course,
                    chapter=chapter,
                    bundle=bundle,
                    task_point=task_point,
                    account=account,
                    control=control,
                    progress=progress,
                    answer_binding=binding,
                )
                if quiz_attention is not None and attention is None:
                    attention = quiz_attention
                continue
            if isinstance(task_point, UnsupportedTaskPoint):
                progress.record_event(
                    chapter.chapter_id,
                    kind="chapter.unsupported_task_point",
                    level="warning",
                    payload={"task_type": "unsupported"},
                )
                attention = ChapterStatus.FAILED
                continue
            if isinstance(task_point, VideoTaskPoint):
                self._run_video(
                    snapshot=snapshot,
                    course=course,
                    chapter=chapter,
                    bundle=bundle,
                    task_point=task_point,
                    account=account,
                    control=control,
                    progress=progress,
                )
                continue
            if isinstance(task_point, DocumentTaskPoint):
                self._run_document(
                    course=course,
                    chapter=chapter,
                    task_point=task_point,
                    account=account,
                    control=control,
                    progress=progress,
                )
                continue
            if isinstance(task_point, ReadTaskPoint):
                self._run_reading(
                    course=course,
                    chapter=chapter,
                    bundle=bundle,
                    task_point=task_point,
                    account=account,
                    control=control,
                    progress=progress,
                )
                continue
            raise PlatformParseError("chapter task point", "unknown typed task point")
        return attention

    def _run_quiz(
        self,
        *,
        snapshot: TaskExecutionSnapshot,
        course: Course,
        chapter: Chapter,
        bundle: ChapterTaskBundle,
        task_point: QuizTaskPoint,
        account: AccountStudySession,
        control: WorkerControl,
        progress: ClaimedTaskProgress,
        answer_binding: AnswerProviderBinding,
    ) -> ChapterStatus | None:
        client = self._quiz_client_factory(account.session)
        result = self._authenticated(
            account,
            lambda _active: client.complete(
                course,
                task_point,
                defaults=bundle.defaults,
                provider=answer_binding.provider,
                course_context=course.title,
                mode=self._quiz_submission_mode(snapshot.config),
                submit_threshold=self._quiz_submit_threshold(snapshot.config),
            ),
            control,
        )
        level = "info" if result.status is QuizSubmissionStatus.SUBMITTED else "warning"
        progress.record_event(
            chapter.chapter_id,
            kind=f"chapter.quiz.{result.status.value}",
            level=level,
            payload={
                "task_type": "quiz",
                "answered_count": result.answered_count,
                "total_questions": result.total_questions,
                "coverage": round(result.coverage, 4),
                "provider_error_count": result.provider_error_count,
                "provider_result_reason": result.reason,
                "reason": (
                    answer_binding.unavailable_reason
                    if answer_binding.unavailable_reason
                    and result.reason == "provider_unconfigured"
                    else result.reason
                ),
            },
        )
        if result.status is QuizSubmissionStatus.SUBMITTED:
            return None
        if result.status is QuizSubmissionStatus.REJECTED:
            raise TaskPointRejected
        return ChapterStatus.UNSUBMITTED

    def _run_video(
        self,
        *,
        snapshot: TaskExecutionSnapshot,
        course: Course,
        chapter: Chapter,
        bundle: ChapterTaskBundle,
        task_point: VideoTaskPoint,
        account: AccountStudySession,
        control: WorkerControl,
        progress: ClaimedTaskProgress,
    ) -> None:
        video_client = self._video_client_factory(account.session)

        def record(kind: str, payload: Mapping[str, object]) -> None:
            progress.record_event(
                chapter.chapter_id,
                kind=f"chapter.{kind}",
                payload=payload,
            )

        runner = self._playback_factory(video_client, control, record)
        report_interval = bundle.defaults.report_time_interval if bundle.defaults else 60
        result = self._authenticated(
            account,
            lambda _active: runner.run(
                course=course,
                task=task_point.media,
                fid=account.fid,
                user_id=account.user_id,
                speed=self._speed(snapshot.config),
                kind=task_point.kind,
                report_interval_seconds=report_interval,
            ),
            control,
        )
        self._record_point_completed(
            progress,
            chapter.chapter_id,
            result.kind.value.casefold(),
        )

    def _run_document(
        self,
        *,
        course: Course,
        chapter: Chapter,
        task_point: DocumentTaskPoint,
        account: AccountStudySession,
        control: WorkerControl,
        progress: ClaimedTaskProgress,
    ) -> None:
        client = self._document_client_factory(account.session)
        result = self._authenticated(
            account,
            lambda _active: client.complete(course, task_point),
            control,
        )
        if not result.accepted:
            raise TaskPointRejected
        self._record_point_completed(progress, chapter.chapter_id, "document")

    def _run_reading(
        self,
        *,
        course: Course,
        chapter: Chapter,
        bundle: ChapterTaskBundle,
        task_point: ReadTaskPoint,
        account: AccountStudySession,
        control: WorkerControl,
        progress: ClaimedTaskProgress,
    ) -> None:
        knowledge_id = (
            bundle.defaults.knowledge_id
            if bundle.defaults and bundle.defaults.knowledge_id
            else chapter.chapter_id
        )
        client = self._reading_client_factory(account.session)
        result = self._authenticated(
            account,
            lambda _active: client.complete(
                course,
                task_point,
                knowledge_id=knowledge_id,
            ),
            control,
        )
        if not result.accepted:
            raise TaskPointRejected
        self._record_point_completed(progress, chapter.chapter_id, "reading")

    @staticmethod
    def _authenticated(
        account: AccountStudySession,
        operation: Callable[[ChaoxingClient], ResultT],
        control: WorkerControl,
    ) -> ResultT:
        control.checkpoint()
        result = account.execute(operation)
        control.checkpoint()
        return result

    @staticmethod
    def _record_point_completed(
        progress: ClaimedTaskProgress,
        chapter_id: str,
        task_type: str,
    ) -> None:
        event_kind = {
            "document": "chapter.document.completed",
            "reading": "chapter.reading.completed",
            "empty_page": "chapter.empty_page.completed",
        }.get(task_type, "chapter.task_point_completed")
        progress.record_event(
            chapter_id,
            kind=event_kind,
            payload={"task_type": task_type},
        )

    @staticmethod
    def _course(snapshot: TaskExecutionSnapshot) -> Course:
        return Course(
            course_id=snapshot.course_id,
            clazz_id=snapshot.class_id,
            cpi=snapshot.cpi,
            title=snapshot.course_title,
        )

    @staticmethod
    def _speed(config: Mapping[str, object]) -> float:
        value = config.get("speed", 1.0)
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            return 1.0
        speed = float(value)
        return speed if 1.0 <= speed <= 2.0 else 1.0

    @staticmethod
    def _chapter_concurrency(config: Mapping[str, object]) -> int:
        value = config.get("chapter_concurrency", 1)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return 1
        # Account settings currently cap this at eight.  Keep a defensive cap for
        # hand-edited/legacy snapshots so a malformed task cannot create unbounded
        # thread pressure.
        return min(value, 8)

    @staticmethod
    def _unopened_policy(config: Mapping[str, object]) -> str:
        value = config.get("unopened_policy", "retry")
        return value if value in {"retry", "skip"} else "retry"

    @staticmethod
    def _quiz_submission_mode(config: Mapping[str, object]) -> QuizSubmissionMode:
        value = config.get("quiz_submission_mode", QuizSubmissionMode.AUTO.value)
        if not isinstance(value, str):
            return QuizSubmissionMode.AUTO
        try:
            return QuizSubmissionMode(value)
        except ValueError:
            return QuizSubmissionMode.AUTO

    @staticmethod
    def _quiz_submit_threshold(config: Mapping[str, object]) -> float:
        value = config.get("quiz_submit_threshold", 0.8)
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            return 0.8
        threshold = float(value)
        return threshold if 0.0 <= threshold <= 1.0 else 0.8

    def _finish_not_open(
        self,
        snapshot: TaskExecutionSnapshot,
        chapter_id: str,
        progress: ClaimedTaskProgress,
    ) -> None:
        progress.finish_chapter(
            chapter_id,
            status=ChapterStatus.SKIPPED_NOT_OPEN,
            reason="chapter_not_open",
            payload={"unopened_policy": self._unopened_policy(snapshot.config)},
        )

    @staticmethod
    def _attention_reason(status: ChapterStatus | None) -> str | None:
        if status is ChapterStatus.UNSUBMITTED:
            return "quiz_requires_answers"
        if status is ChapterStatus.FAILED:
            return "unsupported_task_point"
        return None

    @staticmethod
    def _safe_failure_reason(exc: Exception) -> str:
        if isinstance(exc, PlatformAuthenticationError):
            return "platform_authentication_failed"
        if isinstance(exc, AccountRuntimeError):
            return "account_runtime_unavailable"
        if isinstance(exc, PlatformParseError):
            return "platform_response_invalid"
        if isinstance(exc, TaskPointRejected):
            return "platform_completion_rejected"
        return "platform_request_failed"
