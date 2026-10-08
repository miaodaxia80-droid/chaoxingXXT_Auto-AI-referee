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
    DiscussionTaskPoint,
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


def test_jobid_only_document_card_is_parsed_as_task() -> None:
    # 新版卡片里课件/教案等普通附件也带 jobid, 但没有 job 标记——
    # 它们只是可查看材料, 不是任务点 (平台对 pending 任务统一发 job:true).
    page = parse_task_card_page(
        """<script>mArg={"attachments":[{
        "begins":0,"ends":0,"type":"document","jobid":"1766028012513548",
        "jtoken":"9391ea16a1a80e94c81c49f6434f580e",
        "otherInfo":"nodeId_1087358235-cpi_497744587",
        "mid":"2815876647471766028012073","enc":"ca9512a32e6a3b76d4b3e4276f9e3944",
        "aid":2156424336,
        "property":{"jobid":"1766028012513548","module":"insertdoc",
            "name":"课件.pptx","objectid":"4171653dd2def09d357717a47f452954",
            "pagenum":"22","type":".pptx","title":"课件.pptx"}
        }]};</script>"""
    )
    assert page.tasks == ()
    assert page.attachment_count == 0
    assert page.completed_attachment_count == 0
    assert page.unresolved_attachment_count == 0


def test_job_true_discussion_card_is_the_pending_task() -> None:
    # 真实课程数据: 一章五张分段卡 (课件/教案/案例/讨论/视频),
    # 只有讨论卡带 job:true + property.isJob, 其余都是普通附件或已完成媒体.
    page = parse_task_card_page(
        """<script>mArg={"attachments":[
        {"begins":0,"ends":0,"type":"document","jobid":"1766028012513548",
            "property":{"module":"insertdoc","name":"课件.pptx","objectid":"abc"}},
        {"begins":0,"ends":0,"jobid":"1765241048263797","job":true,"jobid":"1765241048263797",
            "mid":"2774261486341765241048264",
            "otherInfo":"nodeId_1087358235-cpi_497744587",
            "property":{"module":"insertbbs","isJob":true,
                "title":"数字技术对传统产业升级有何作用\uff1f","mid":"2774261486341765241048264"}},
        {"type":"video","jobid":"1784562160588201","isPassed":true,
            "mid":"9050588167591784562160462","objectId":"vid-obj",
            "property":{"module":"insertvideo","name":"视频.mp4"}}
        ]};</script>"""
    )
    assert page.attachment_count == 2
    assert page.completed_attachment_count == 1
    assert page.unresolved_attachment_count == 0
    assert len(page.tasks) == 1
    task = cast(DiscussionTaskPoint, page.tasks[0])
    assert task.job_id == "1765241048263797"
    assert task.mid == "2774261486341765241048264"
    assert task.title == "数字技术对传统产业升级有何作用？"  # noqa: RUF001


def test_completed_isJob_card_counts_as_finished_attachment() -> None:
    # 讨论任务完成后平台撤掉 job 标记但保留 property.isJob——
    # 它仍是任务附件, 只是已完成.
    page = parse_task_card_page(
        """<script>mArg={"attachments":[{
        "begins":0,"ends":0,"jobid":"1766296524413677",
        "mid":"6864150493591766296524415",
        "property":{"module":"insertbbs","isJob":true,"title":"创意讨论"}
        }]};</script>"""
    )
    assert page.tasks == ()
    assert page.attachment_count == 1
    assert page.completed_attachment_count == 1


def test_insertbbs_discussion_cards_are_parsed_as_tasks() -> None:
    # 章节讨论卡片 type 为空, 模块名在 property.module 上;
    # detail 只在渲染后的 iframe data 属性里, 需从页面 HTML 中提取.
    page = parse_task_card_page(
        """<p><iframe class="ans-module ans-insertbbs-module" module="insertbbs"
        data="{&quot;title&quot;:&quot;Discussion A&quot;,
        &quot;detail&quot;:&quot;请思考并回复&quot;,&quot;mid&quot;:&quot;6864150493591766296524415&quot;,
        &quot;jobid&quot;:&quot;1766296524413677&quot;}"
        ></iframe></p>
        <script>mArg={"attachments":[{
        "begins":0,"ends":0,"job":true,"jobid":"1766296524413677",
        "otherInfo":"nodeId_1087365292-cpi_497744587",
        "mid":"6864150493591766296524415","aid":2157718628,
        "property":{"jobid":"1766296524413677","module":"insertbbs",
            "title":"Discussion A","isJob":true,"replytimes":"1"}
        },{
        "job":true,"jobid":"1766296864920609","mid":"16033776146381766296864921",
        "otherInfo":"nodeId_1087365292-cpi_497744587",
        "property":{"module":"inserttopic","title":"Discussion B","isJob":true}
        },{
        "job":true,"jobid":"missing-topic","property":{"module":"insertbbs"}
        }]};</script>"""
    )

    assert page.attachment_count == 3
    assert page.unresolved_attachment_count == 0
    first = cast(DiscussionTaskPoint, page.tasks[0])
    assert first.job_id == "1766296524413677"
    assert first.mid == "6864150493591766296524415"
    assert first.title == "Discussion A"
    assert first.detail == "请思考并回复"
    assert first.other_info == "nodeId_1087365292-cpi_497744587"
    second = cast(DiscussionTaskPoint, page.tasks[1])
    assert second.mid == "16033776146381766296864921"
    unsupported = cast(UnsupportedTaskPoint, page.tasks[2])
    assert unsupported.job_id == "missing-topic"
    assert unsupported.reason == "missing discussion topic id"


def test_numeric_defaults_and_jobid_are_coerced_to_text() -> None:
    page = parse_task_card_page(
        """<script>mArg={
        "defaults":{"ktoken":"k","knowledgeid":1087374901,"cpi":497744587,"cardid":1068636829},
        "attachments":[{"job":true,"type":"workid","jobid":"work-1"}]
        };</script>"""
    )
    assert page.defaults is not None
    assert page.defaults.knowledge_id == "1087374901"
    assert page.defaults.cpi == "497744587"
    assert page.defaults.card_id == "1068636829"


def test_client_probes_declared_cards_and_stops_after_empty_page() -> None:
    # ``job_count`` counts pending jobs, not card pages, so probing continues
    # until two consecutive cards come back empty.
    session = StubSession(
        [
            response(fixture("task_cards.html")),
            response("<html></html>"),
            response("<html></html>"),
        ]
    )
    client = ChapterTaskClient(session=cast(requests.Session, session))

    bundle = client.fetch(course(), chapter(job_count=1))

    assert len(bundle.tasks) == 5
    assert bundle.defaults is not None
    assert len(session.calls) == 3
    assert session.calls[0][2]["params"]["num"] == 0
    assert session.calls[1][2]["params"]["num"] == 1
    assert session.calls[2][2]["params"]["num"] == 2
    assert all(call[2]["verify"] is True for call in session.calls)


def test_client_scans_past_job_count_to_find_pending_cards() -> None:
    # A chapter may report job_count=1 while the pending job lives on a later
    # card page (e.g. the 讨论 section after 课件/教案/案例).
    session = StubSession(
        [
            response("<script>mArg={\"attachments\":[{\"type\":\"document\",\"jobid\":\"d1\",\"property\":{\"module\":\"insertdoc\",\"objectid\":\"o\"}}]};</script>"),
            response("<script>mArg={\"attachments\":[{\"type\":\"document\",\"jobid\":\"d2\",\"property\":{\"module\":\"insertdoc\",\"objectid\":\"o\"}}]};</script>"),
            response("<script>mArg={\"attachments\":[{\"job\":true,\"jobid\":\"bbs-1\",\"mid\":\"m-1\",\"property\":{\"module\":\"insertbbs\",\"isJob\":true,\"title\":\"讨论\"}}]};</script>"),
            response("<script>mArg={\"attachments\":[{\"type\":\"video\",\"jobid\":\"v1\",\"isPassed\":true,\"objectId\":\"o\",\"mid\":\"m\",\"property\":{\"module\":\"insertvideo\"}}]};</script>"),
            response("<html></html>"),
            response("<html></html>"),
        ]
    )
    client = ChapterTaskClient(session=cast(requests.Session, session))

    bundle = client.fetch(course(), chapter(job_count=1))

    assert len(session.calls) == 6
    assert len(bundle.tasks) == 1
    assert isinstance(bundle.tasks[0], DiscussionTaskPoint)
    assert bundle.completed_attachment_count == 1


def test_client_scans_past_empty_pages_until_pending_jobs_found() -> None:
    # Sections can render empty card pages between materials; the pending job
    # card must still be reached while the declared quota is unmet.
    session = StubSession(
        [
            response("<script>mArg={\"attachments\":[{\"type\":\"document\",\"jobid\":\"d1\",\"property\":{\"module\":\"insertdoc\",\"objectid\":\"o\"}}]};</script>"),
            response("<html></html>"),
            response("<html></html>"),
            response("<script>mArg={\"attachments\":[{\"job\":true,\"jobid\":\"bbs-1\",\"mid\":\"m-1\",\"property\":{\"module\":\"insertbbs\",\"isJob\":true,\"title\":\"讨论\"}}]};</script>"),
            response("<html></html>"),
            response("<html></html>"),
        ]
    )
    client = ChapterTaskClient(session=cast(requests.Session, session))

    bundle = client.fetch(course(), chapter(job_count=1))

    assert len(session.calls) == 6
    assert len(bundle.tasks) == 1
    assert isinstance(bundle.tasks[0], DiscussionTaskPoint)
    assert bundle.material_types == ("document/insertdoc",)


def test_client_stops_empty_probe_when_no_jobs_declared() -> None:
    session = StubSession(
        [
            response("<script>mArg={\"attachments\":[{\"type\":\"document\",\"jobid\":\"d1\",\"property\":{\"module\":\"insertdoc\",\"objectid\":\"o\"}}]};</script>"),
            response("<html></html>"),
            response("<html></html>"),
        ]
    )
    client = ChapterTaskClient(session=cast(requests.Session, session))

    bundle = client.fetch(course(), chapter(job_count=0))

    assert len(session.calls) == 3
    assert not bundle.tasks


def test_client_detects_login_redirect_without_exposing_page() -> None:
    session = StubSession(
        [response("用户登录 private-page", url="https://passport2.chaoxing.com/login")]
    )
    client = ChapterTaskClient(session=cast(requests.Session, session))
    with pytest.raises(PlatformAuthenticationError, match="not authenticated") as raised:
        client.fetch(course(), chapter())
    assert "private-page" not in str(raised.value)
