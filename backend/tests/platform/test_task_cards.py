from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, cast

import pytest
import requests

from chaoxing_app.platform.errors import PlatformAuthenticationError, PlatformParseError
from chaoxing_app.platform.models import Chapter, Course
from chaoxing_app.platform.task_points.cards import (
    ChapterTaskClient,
    DocumentTaskPoint,
    QuizTaskPoint,
    ReadTaskPoint,
    UnsupportedTaskPoint,
    VideoTaskPoint,
    parse_task_card_page,
)
from chaoxing_app.platform.task_points.video import MediaKind

FIXTURES = Path(__file__).with_name("fixtures")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def response(html: str, *, url: str = "https://sanitized.example.test/cards") -> requests.Response:
    result = requests.Response()
    result.status_code = 200
    result.url = url
    result.encoding = "utf-8"
    result._content = html.encode("utf-8")
    return result


class StubSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append((method, url, kwargs))
        return self.responses.popleft()


def course() -> Course:
    return Course("course-1", "class-1", "cpi-1", "Fixture Course")


def chapter(job_count: int = 1) -> Chapter:
    return Chapter("chapter-1", "Fixture Chapter", job_count, False, False)


def test_parses_typed_task_points_defaults_and_completed_count() -> None:
    page = parse_task_card_page(fixture("task_cards.html"))

    assert page.not_open is False
    assert page.attachment_count == 6
    assert page.completed_attachment_count == 1
    assert page.defaults is not None
    assert page.defaults.report_time_interval == 45
    assert [type(task) for task in page.tasks] == [
        VideoTaskPoint,
        DocumentTaskPoint,
        QuizTaskPoint,
        ReadTaskPoint,
        UnsupportedTaskPoint,
    ]
    video = cast(VideoTaskPoint, page.tasks[0])
    assert video.media.initial_play_time_seconds == 15
    assert video.media.other_info == "nodeId_42-rt_d"
    assert video.media.face_capture_enc == "face-enc"
    assert video.media.allow_audio_fallback is True
    unsupported = cast(UnsupportedTaskPoint, page.tasks[-1])
    assert unsupported.raw_type == "live"


def test_balanced_marg_parser_handles_nested_strings_and_reports_invalid_json() -> None:
    page = parse_task_card_page(
        '<script>mArg={"defaults":{"ktoken":"value } in string"},"attachments":[]};</script>'
    )
    assert page.defaults is not None
    assert page.defaults.ktoken == "value } in string"
    with pytest.raises(PlatformParseError, match="invalid JSON"):
        parse_task_card_page('<script>mArg={"attachments": [oops]};</script>')


def test_marg_parser_skips_scalar_placeholder_before_object_assignment() -> None:
    page = parse_task_card_page(
        """<script>
        var mArg = "";
        mArg = {"defaults":{"ktoken":"active"},"attachments":[]};
        </script>"""
    )

    assert page.defaults is not None
    assert page.defaults.ktoken == "active"
    assert parse_task_card_page('<script>var mArg = "";</script>').is_empty is True


def test_media_kind_detects_audio_without_changing_the_platform_card_type() -> None:
    page = parse_task_card_page(
        """<script>mArg={"attachments":[{
        "job":true,"type":"video","jobid":"audio-job","objectId":"object-1",
        "mid":"mid-1","property":{"name":"Lecture.MP3"}
        }]};</script>"""
    )

    task = cast(VideoTaskPoint, page.tasks[0])
    assert task.kind is MediaKind.AUDIO
    assert task.media.allow_audio_fallback is False


def test_explicit_video_filename_disables_audio_fallback() -> None:
    page = parse_task_card_page(
        """<script>mArg={"attachments":[{
        "job":true,"type":"video","jobid":"video-job","objectId":"object-1",
        "mid":"mid-1","property":{"name":"Lecture.MP4"}
        }]};</script>"""
    )

    task = cast(VideoTaskPoint, page.tasks[0])
    assert task.kind is MediaKind.VIDEO
    assert task.media.allow_audio_fallback is False


def test_not_open_and_missing_marg_are_distinct() -> None:
    assert parse_task_card_page("章节未开放").not_open is True
    empty = parse_task_card_page("<html>no task data</html>")
    assert empty.not_open is False
    assert empty.is_empty is True


def test_non_job_attachment_is_ignored_but_incomplete_job_is_retained() -> None:
    page = parse_task_card_page(
        """<script>mArg={"attachments":[
        {"type":"","property":{},"isPassed":false},
        {"type":"read","property":{"read":true},"isPassed":false},
        {"type":"workid","job":true,"jobid":"quiz-job"},
        {"type":"video","job":true}
        ]};</script>"""
    )
    assert page.attachment_count == 2
    assert page.completed_attachment_count == 0
    assert page.unresolved_attachment_count == 0
    assert len(page.tasks) == 2
    assert isinstance(page.tasks[-1], UnsupportedTaskPoint)


def test_client_probes_declared_cards_and_stops_after_empty_page() -> None:
    session = StubSession([response(fixture("task_cards.html")), response("<html></html>")])
    client = ChapterTaskClient(session=cast(requests.Session, session))

    bundle = client.fetch(course(), chapter(job_count=1))

    assert len(bundle.tasks) == 5
    assert bundle.defaults is not None
    assert len(session.calls) == 2
    assert session.calls[0][2]["params"]["num"] == 0
    assert session.calls[1][2]["params"]["num"] == 1
    assert all(call[2]["verify"] is True for call in session.calls)


def test_client_detects_login_redirect_without_exposing_page() -> None:
    session = StubSession(
        [response("用户登录 private-page", url="https://passport2.chaoxing.com/login")]
    )
    client = ChapterTaskClient(session=cast(requests.Session, session))
    with pytest.raises(PlatformAuthenticationError, match="not authenticated") as raised:
        client.fetch(course(), chapter())
    assert "private-page" not in str(raised.value)
