from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from chaoxing_app.platform.errors import PlatformError, PlatformHTTPError
from chaoxing_app.platform.models import Course
from chaoxing_app.platform.task_points.video import (
    MediaKind,
    MediaMetadata,
    MediaTask,
    ProgressReportResult,
)
from chaoxing_app.worker.control import WorkerControl


class MediaCompletionError(PlatformError):
    """Raised when final progress reports never mark the media task as passed."""


class MediaProgressPort(Protocol):
    def fetch_metadata(
        self,
        task: MediaTask,
        *,
        fid: str,
        kind: MediaKind,
    ) -> MediaMetadata: ...

    def report_progress(
        self,
        *,
        course: Course,
        task: MediaTask,
        metadata: MediaMetadata,
        user_id: str,
        playing_time_seconds: int,
        kind: MediaKind,
    ) -> ProgressReportResult: ...


ProgressCallback = Callable[[str, Mapping[str, object]], None]


def _ignore_progress(_kind: str, _payload: Mapping[str, object]) -> None:
    return None


@dataclass(frozen=True, slots=True)
class PlaybackResult:
    duration_seconds: int
    final_play_time_seconds: int
    report_count: int
    kind: MediaKind = MediaKind.VIDEO


@dataclass(slots=True)
class _PlaybackState:
    metadata: MediaMetadata = field(repr=False)
    kind: MediaKind
    audio_fallback_allowed: bool
    forbidden_recoveries: int = 0
    video_metadata_refreshed: bool = False


@dataclass(frozen=True, slots=True)
class _ReportOutcome:
    result: ProgressReportResult
    attempts: int
    playing_time_seconds: int


class MediaPlaybackRunner:
    def __init__(
        self,
        *,
        client: MediaProgressPort,
        control: WorkerControl,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        progress: ProgressCallback = _ignore_progress,
        checkpoint_interval_seconds: float = 1.0,
        final_retry_delay_seconds: float = 2.0,
        final_report_attempts: int = 3,
        forbidden_recovery_attempts: int = 2,
    ) -> None:
        if checkpoint_interval_seconds <= 0:
            raise ValueError("checkpoint interval must be positive")
        if final_retry_delay_seconds < 0:
            raise ValueError("final retry delay must not be negative")
        if final_report_attempts < 1:
            raise ValueError("final_report_attempts must be positive")
        if not 0 <= forbidden_recovery_attempts <= 3:
            raise ValueError("forbidden_recovery_attempts must be between zero and three")
        self._client = client
        self._control = control
        self._monotonic = monotonic
        self._sleep = sleep
        self._progress = progress
        self._checkpoint_interval = checkpoint_interval_seconds
        self._final_retry_delay = final_retry_delay_seconds
        self._final_report_attempts = final_report_attempts
        self._forbidden_recovery_attempts = forbidden_recovery_attempts

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
        if not 1.0 <= speed <= 2.0:
            raise ValueError("media speed must be between 1.0 and 2.0")
        if report_interval_seconds <= 0:
            raise ValueError("report interval must be positive")
        state = self._initial_state(task=task, fid=fid, kind=kind)
        duration = state.metadata.duration_seconds
        play_time = min(float(task.initial_play_time_seconds), float(duration))
        report_interval = max(report_interval_seconds, 5)
        report_count = 0
        last_reported = int(play_time)

        self._control.checkpoint()
        initial = self._report(
            course=course,
            task=task,
            state=state,
            fid=fid,
            user_id=user_id,
            play_time=int(play_time),
        )
        report_count += initial.attempts
        duration = state.metadata.duration_seconds
        play_time = min(play_time, float(duration))
        last_reported = int(play_time)
        event_prefix = state.kind.value.casefold()
        self._progress(
            f"{event_prefix}.started",
            {
                "duration": duration,
                "play_time": int(play_time),
                "kind": state.kind.value,
            },
        )
        if initial.result.is_passed:
            return PlaybackResult(duration, initial.playing_time_seconds, report_count, state.kind)

        last_tick = self._monotonic()
        while play_time < duration:
            self._control.checkpoint()
            remaining_real_seconds = (duration - play_time) / speed
            self._sleep(min(self._checkpoint_interval, remaining_real_seconds))
            now = self._monotonic()
            elapsed = max(now - last_tick, 0.0)
            last_tick = now
            play_time = min(play_time + elapsed * speed, float(duration))
            integer_play_time = int(play_time)
            should_report = (
                integer_play_time >= duration
                or integer_play_time - last_reported >= report_interval
            )
            if should_report:
                self._control.checkpoint()
                outcome = self._report(
                    course=course,
                    task=task,
                    state=state,
                    fid=fid,
                    user_id=user_id,
                    play_time=integer_play_time,
                )
                report_count += outcome.attempts
                duration = state.metadata.duration_seconds
                play_time = min(play_time, float(duration))
                integer_play_time = int(play_time)
                last_reported = integer_play_time
                event_prefix = state.kind.value.casefold()
                self._progress(
                    f"{event_prefix}.progress",
                    {
                        "duration": duration,
                        "play_time": integer_play_time,
                        "remaining": max(duration - integer_play_time, 0),
                        "kind": state.kind.value,
                    },
                )
                if outcome.result.is_passed:
                    return PlaybackResult(
                        duration,
                        outcome.playing_time_seconds,
                        report_count,
                        state.kind,
                    )

        for attempt in range(1, self._final_report_attempts):
            self._control.checkpoint()
            if self._final_retry_delay:
                self._sleep(self._final_retry_delay)
            self._control.checkpoint()
            outcome = self._report(
                course=course,
                task=task,
                state=state,
                fid=fid,
                user_id=user_id,
                play_time=duration,
            )
            report_count += outcome.attempts
            duration = state.metadata.duration_seconds
            if outcome.result.is_passed:
                return PlaybackResult(
                    duration,
                    outcome.playing_time_seconds,
                    report_count,
                    state.kind,
                )
            event_prefix = state.kind.value.casefold()
            self._progress(
                f"{event_prefix}.completion_retry",
                {"attempt": attempt + 1, "kind": state.kind.value},
            )
        raise MediaCompletionError(
            f"media was not accepted after {self._final_report_attempts} final reports"
        )

    def _report(
        self,
        *,
        course: Course,
        task: MediaTask,
        state: _PlaybackState,
        fid: str,
        user_id: str,
        play_time: int,
    ) -> _ReportOutcome:
        attempts = 0
        while True:
            reported_time = min(play_time, state.metadata.duration_seconds)
            try:
                attempts += 1
                result = self._client.report_progress(
                    course=course,
                    task=task,
                    metadata=state.metadata,
                    user_id=user_id,
                    playing_time_seconds=reported_time,
                    kind=state.kind,
                )
            except PlatformHTTPError as exc:
                if (
                    exc.status_code != 403
                    or state.forbidden_recoveries >= self._forbidden_recovery_attempts
                ):
                    raise
                self._refresh_after_forbidden(task=task, state=state, fid=fid)
                continue
            return _ReportOutcome(result, attempts, reported_time)

    def _initial_state(
        self,
        *,
        task: MediaTask,
        fid: str,
        kind: MediaKind,
    ) -> _PlaybackState:
        try:
            metadata = self._client.fetch_metadata(task, fid=fid, kind=kind)
        except PlatformHTTPError as exc:
            if (
                exc.status_code != 403
                or kind is not MediaKind.VIDEO
                or not task.allow_audio_fallback
            ):
                raise
            self._control.checkpoint()
            metadata = self._client.fetch_metadata(task, fid=fid, kind=MediaKind.AUDIO)
            return _PlaybackState(
                metadata=metadata,
                kind=metadata.kind or MediaKind.AUDIO,
                audio_fallback_allowed=False,
            )

        active_kind = metadata.kind or kind
        return _PlaybackState(
            metadata=metadata,
            kind=active_kind,
            audio_fallback_allowed=(
                task.allow_audio_fallback
                and kind is MediaKind.VIDEO
                and metadata.kind is None
            ),
        )

    def _refresh_after_forbidden(
        self,
        *,
        task: MediaTask,
        state: _PlaybackState,
        fid: str,
    ) -> None:
        while state.forbidden_recoveries < self._forbidden_recovery_attempts:
            refresh_kind = self._refresh_kind(state)
            state.forbidden_recoveries += 1
            if refresh_kind is MediaKind.VIDEO and state.audio_fallback_allowed:
                state.video_metadata_refreshed = True
            self._control.checkpoint()
            try:
                metadata = self._client.fetch_metadata(task, fid=fid, kind=refresh_kind)
            except PlatformHTTPError as exc:
                if exc.status_code == 403:
                    continue
                raise

            state.metadata = metadata
            state.kind = metadata.kind or refresh_kind
            if metadata.kind is not None or refresh_kind is MediaKind.AUDIO:
                state.audio_fallback_allowed = False
            self._control.checkpoint()
            return
        raise PlatformHTTPError("media metadata refresh", 403)

    @staticmethod
    def _refresh_kind(state: _PlaybackState) -> MediaKind:
        if (
            state.audio_fallback_allowed
            and state.kind is MediaKind.VIDEO
            and state.video_metadata_refreshed
        ):
            return MediaKind.AUDIO
        return state.kind
