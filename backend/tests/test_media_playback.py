from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from chaoxing_app.platform.errors import PlatformHTTPError
from chaoxing_app.platform.models import Course
from chaoxing_app.platform.task_points.video import (
    MediaKind,
    MediaMetadata,
    MediaTask,
    ProgressReportResult,
)
from chaoxing_app.worker.media_playback import MediaCompletionError, MediaPlaybackRunner


class FakeControl:
    def __init__(self) -> None:
        self.checkpoints = 0

    def checkpoint(self) -> None:
        self.checkpoints += 1


@dataclass
class FakeClock:
    value: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class FakeMediaClient:
    def __init__(
        self,
        *,
        duration: int,
        pass_on_report: int,
        metadata_kind: MediaKind | None = None,
    ) -> None:
        self.duration = duration
        self.pass_on_report = pass_on_report
        self.metadata_kind = metadata_kind
        self.reports: list[int] = []
        self.report_kinds: list[MediaKind] = []

    def fetch_metadata(
        self, _task: MediaTask, *, fid: str, kind: MediaKind
    ) -> MediaMetadata:
        assert fid == "fid-1"
        assert kind is MediaKind.VIDEO
        return MediaMetadata(
            dtoken="fixture-token",
            duration_seconds=self.duration,
            kind=self.metadata_kind,
        )

    def report_progress(
        self,
        *,
        course: Course,
        task: MediaTask,
        metadata: MediaMetadata,
        user_id: str,
        playing_time_seconds: int,
        kind: MediaKind,
    ) -> ProgressReportResult:
        assert course.course_id == "course-1"
        assert task.job_id == "job-1"
        assert metadata.duration_seconds == self.duration
        assert user_id == "user-1"
        self.reports.append(playing_time_seconds)
        self.report_kinds.append(kind)
        return ProgressReportResult(
            is_passed=len(self.reports) >= self.pass_on_report,
            rt="0.9",
        )


class ScriptedMediaClient:
    def __init__(
        self,
        *,
        metadata_results: list[MediaMetadata | Exception],
        report_results: list[ProgressReportResult | Exception],
    ) -> None:
        self.metadata_results = list(metadata_results)
        self.report_results = list(report_results)
        self.fetch_kinds: list[MediaKind] = []
        self.reports: list[tuple[MediaKind, int, str]] = []

    def fetch_metadata(
        self, _task: MediaTask, *, fid: str, kind: MediaKind
    ) -> MediaMetadata:
        assert fid == "fid-1"
        self.fetch_kinds.append(kind)
        result = self.metadata_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def report_progress(
        self,
        *,
        course: Course,
        task: MediaTask,
        metadata: MediaMetadata,
        user_id: str,
        playing_time_seconds: int,
        kind: MediaKind,
    ) -> ProgressReportResult:
        assert course.course_id == "course-1"
        assert task.job_id == "job-1"
        assert user_id == "user-1"
        self.reports.append((kind, playing_time_seconds, metadata.dtoken))
        result = self.report_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def course() -> Course:
    return Course("course-1", "class-1", "cpi-1", "Fixture Course")


def task(initial: int = 0, *, allow_audio_fallback: bool = False) -> MediaTask:
    return MediaTask(
        job_id="job-1",
        object_id="object-1",
        other_info="node-rt_d",
        initial_play_time_seconds=initial,
        allow_audio_fallback=allow_audio_fallback,
    )


def runner(client: FakeMediaClient, clock: FakeClock, control: FakeControl):
    events: list[tuple[str, dict[str, object]]] = []
    instance = MediaPlaybackRunner(
        client=client,
        control=control,  # type: ignore[arg-type]
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        progress=lambda kind, payload: events.append((kind, dict(payload))),
        checkpoint_interval_seconds=1,
        final_retry_delay_seconds=2,
        final_report_attempts=3,
    )
    return instance, events


def test_playback_advances_only_with_real_elapsed_time_and_configured_speed() -> None:
    client = FakeMediaClient(duration=10, pass_on_report=3)
    clock = FakeClock()
    control = FakeControl()
    playback, events = runner(client, clock, control)

    result = playback.run(
        course=course(),
        task=task(),
        fid="fid-1",
        user_id="user-1",
        speed=2.0,
        kind=MediaKind.VIDEO,
        report_interval_seconds=5,
    )

    assert sum(clock.sleeps) == 5.0
    assert client.reports == [0, 6, 10]
    assert result.final_play_time_seconds == 10
    assert result.report_count == 3
    assert control.checkpoints >= 6
    assert events[0][0] == "video.started"
    assert events[-1][1]["play_time"] == 10


def test_already_passed_initial_report_does_not_sleep_or_jump_to_end() -> None:
    client = FakeMediaClient(duration=120, pass_on_report=1)
    clock = FakeClock()
    control = FakeControl()
    playback, _events = runner(client, clock, control)

    result = playback.run(
        course=course(),
        task=task(initial=15),
        fid="fid-1",
        user_id="user-1",
        speed=1.0,
        kind=MediaKind.VIDEO,
        report_interval_seconds=60,
    )

    assert client.reports == [15]
    assert clock.sleeps == []
    assert result.final_play_time_seconds == 15


def test_metadata_audio_hint_controls_reports_and_event_names() -> None:
    client = FakeMediaClient(
        duration=120,
        pass_on_report=1,
        metadata_kind=MediaKind.AUDIO,
    )
    clock = FakeClock()
    playback, events = runner(client, clock, FakeControl())

    result = playback.run(
        course=course(),
        task=task(),
        fid="fid-1",
        user_id="user-1",
        speed=1.0,
        kind=MediaKind.VIDEO,
        report_interval_seconds=60,
    )

    assert result.kind is MediaKind.AUDIO
    assert client.report_kinds == [MediaKind.AUDIO]
    assert events[0][0] == "audio.started"


def test_initial_video_metadata_403_tries_audio_once_without_waiting() -> None:
    client = ScriptedMediaClient(
        metadata_results=[
            PlatformHTTPError("media metadata", 403),
            MediaMetadata("audio-token", 120),
        ],
        report_results=[ProgressReportResult(True, "0.9")],
    )
    clock = FakeClock()
    playback, events = runner(client, clock, FakeControl())  # type: ignore[arg-type]

    result = playback.run(
        course=course(),
        task=task(allow_audio_fallback=True),
        fid="fid-1",
        user_id="user-1",
        speed=1.0,
        kind=MediaKind.VIDEO,
        report_interval_seconds=60,
    )

    assert client.fetch_kinds == [MediaKind.VIDEO, MediaKind.AUDIO]
    assert [(kind, play_time) for kind, play_time, _token in client.reports] == [
        (MediaKind.AUDIO, 0)
    ]
    assert clock.sleeps == []
    assert result.kind is MediaKind.AUDIO
    assert events[0][0] == "audio.started"


def test_forbidden_progress_refreshes_metadata_and_uses_the_new_token() -> None:
    client = ScriptedMediaClient(
        metadata_results=[
            MediaMetadata("expired-token", 10, MediaKind.VIDEO),
            MediaMetadata("fresh-token", 10, MediaKind.VIDEO),
        ],
        report_results=[
            PlatformHTTPError("media progress report", 403),
            ProgressReportResult(False, "0.9"),
            ProgressReportResult(True, "0.9"),
        ],
    )
    clock = FakeClock()
    playback, events = runner(client, clock, FakeControl())  # type: ignore[arg-type]

    result = playback.run(
        course=course(),
        task=task(),
        fid="fid-1",
        user_id="user-1",
        speed=1.0,
        kind=MediaKind.VIDEO,
        report_interval_seconds=10,
    )

    assert client.fetch_kinds == [MediaKind.VIDEO, MediaKind.VIDEO]
    assert [token for _kind, _play_time, token in client.reports] == [
        "expired-token",
        "fresh-token",
        "fresh-token",
    ]
    assert result.report_count == 3
    assert sum(clock.sleeps) == 10
    assert "expired-token" not in repr(events)
    assert "fresh-token" not in repr(events)


def test_ambiguous_media_falls_back_at_current_progress_without_double_wait() -> None:
    client = ScriptedMediaClient(
        metadata_results=[
            MediaMetadata("initial-video-token", 10),
            MediaMetadata("refreshed-video-token", 10),
            MediaMetadata("audio-token", 10),
        ],
        report_results=[
            ProgressReportResult(False, "0.9"),
            PlatformHTTPError("media progress report", 403),
            PlatformHTTPError("media progress report", 403),
            ProgressReportResult(False, "0.9"),
            ProgressReportResult(True, "0.9"),
        ],
    )
    clock = FakeClock()
    playback, events = runner(client, clock, FakeControl())  # type: ignore[arg-type]

    result = playback.run(
        course=course(),
        task=task(allow_audio_fallback=True),
        fid="fid-1",
        user_id="user-1",
        speed=1.0,
        kind=MediaKind.VIDEO,
        report_interval_seconds=5,
    )

    assert client.fetch_kinds == [
        MediaKind.VIDEO,
        MediaKind.VIDEO,
        MediaKind.AUDIO,
    ]
    assert [(kind, play_time) for kind, play_time, _token in client.reports] == [
        (MediaKind.VIDEO, 0),
        (MediaKind.VIDEO, 5),
        (MediaKind.VIDEO, 5),
        (MediaKind.AUDIO, 5),
        (MediaKind.AUDIO, 10),
    ]
    assert sum(clock.sleeps) == 10
    assert result.kind is MediaKind.AUDIO
    assert result.report_count == 5
    assert events[0][0] == "video.started"
    assert events[-1][0] == "audio.progress"


def test_forbidden_recovery_is_bounded_and_known_video_never_tries_audio() -> None:
    client = ScriptedMediaClient(
        metadata_results=[
            MediaMetadata("initial-secret", 10, MediaKind.VIDEO),
            MediaMetadata("refresh-secret-1", 10, MediaKind.VIDEO),
            MediaMetadata("refresh-secret-2", 10, MediaKind.VIDEO),
        ],
        report_results=[
            PlatformHTTPError("media progress report", 403),
            PlatformHTTPError("media progress report", 403),
            PlatformHTTPError("media progress report", 403),
        ],
    )
    clock = FakeClock()
    playback, _events = runner(client, clock, FakeControl())  # type: ignore[arg-type]

    with pytest.raises(PlatformHTTPError, match="HTTP 403") as raised:
        playback.run(
            course=course(),
            task=task(),
            fid="fid-1",
            user_id="user-1",
            speed=1.0,
            kind=MediaKind.VIDEO,
            report_interval_seconds=5,
        )

    assert client.fetch_kinds == [MediaKind.VIDEO, MediaKind.VIDEO, MediaKind.VIDEO]
    assert len(client.reports) == 3
    assert clock.sleeps == []
    rendered = f"{raised.value!r} {raised.value}"
    for secret in ("initial-secret", "refresh-secret-1", "refresh-secret-2"):
        assert secret not in rendered


def test_final_report_retries_are_bounded() -> None:
    client = FakeMediaClient(duration=2, pass_on_report=99)
    clock = FakeClock()
    control = FakeControl()
    playback, events = runner(client, clock, control)

    with pytest.raises(MediaCompletionError, match="after 3 final reports"):
        playback.run(
            course=course(),
            task=task(),
            fid="fid-1",
            user_id="user-1",
            speed=1.0,
            kind=MediaKind.VIDEO,
            report_interval_seconds=60,
        )

    assert client.reports == [0, 2, 2, 2]
    assert sum(clock.sleeps) == 6.0
    assert [kind for kind, _payload in events].count("video.completion_retry") == 2


@pytest.mark.parametrize("speed", [0.9, 2.1])
def test_invalid_speed_is_rejected_before_network_or_sleep(speed: float) -> None:
    client = FakeMediaClient(duration=10, pass_on_report=1)
    clock = FakeClock()
    playback, _events = runner(client, clock, FakeControl())
    with pytest.raises(ValueError, match=r"between 1\.0 and 2\.0"):
        playback.run(
            course=course(),
            task=task(),
            fid="fid-1",
            user_id="user-1",
            speed=speed,
            kind=MediaKind.VIDEO,
            report_interval_seconds=60,
        )
    assert client.reports == []
    assert clock.sleeps == []
