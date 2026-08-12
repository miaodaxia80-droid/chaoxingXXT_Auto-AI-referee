from __future__ import annotations

import json
from collections import deque
from typing import Any, cast

import pytest
import requests

from chaoxing_app.platform.errors import (
    PlatformCaptchaError,
    PlatformConfigurationError,
    PlatformHTTPError,
)
from chaoxing_app.platform.models import Course
from chaoxing_app.platform.task_points.video import (
    MediaKind,
    MediaMetadata,
    MediaTask,
    VideoProgressClient,
    compute_progress_signature,
    resolve_rt_candidates,
)


def response(payload: object, status_code: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status_code
    result.url = "https://sanitized.example.test/result"
    result.encoding = "utf-8"
    result._content = json.dumps(payload).encode("utf-8")
    return result


class StubSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append((method, url, kwargs))
        return self.responses.popleft()


class StubCaptchaSolver:
    def __init__(self) -> None:
        self.calls = 0

    def solve(self) -> None:
        self.calls += 1


def fixture_course() -> Course:
    return Course(
        course_id="course-100",
        clazz_id="clazz-100",
        cpi="cpi-100",
        title="Fixture Course",
    )


def fixture_task(**changes: object) -> MediaTask:
    values: dict[str, object] = {
        "job_id": "job-7",
        "object_id": "object-9",
        "other_info": "nodeId_42-rt_d",
        "name": "Fixture Video",
    }
    values.update(changes)
    return MediaTask(**values)  # type: ignore[arg-type]


def test_progress_signature_matches_legacy_protocol_vector() -> None:
    signature = compute_progress_signature(
        class_id="clazz-100",
        user_id="user-42",
        job_id="job-7",
        object_id="object-9",
        playing_time_seconds=30,
        duration_seconds=120,
    )
    assert signature == "b930b1e3c5bff0de8e097c682df119ed"


def test_rt_resolution_prefers_explicit_then_marker_then_compatibility_pair() -> None:
    assert resolve_rt_candidates("1.2", "-rt_d") == ("1.2",)
    assert resolve_rt_candidates("", "node-rt_d") == ("0.9",)
    assert resolve_rt_candidates("", "node-rt_1") == ("1",)
    assert resolve_rt_candidates("", "node") == ("0.9", "1")


def test_fetches_typed_metadata_with_tls_verification() -> None:
    session = StubSession([response({"status": "success", "dtoken": "token", "duration": 120})])
    client = VideoProgressClient(session=cast(requests.Session, session))

    metadata = client.fetch_metadata(fixture_task(), fid="fid-1", kind=MediaKind.VIDEO)

    assert metadata == MediaMetadata(dtoken="token", duration_seconds=120)
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/object-9")
    assert kwargs["params"] == {"k": "fid-1", "flag": "normal"}
    assert kwargs["verify"] is True


def test_metadata_can_correct_a_media_kind_from_the_filename() -> None:
    session = StubSession(
        [
            response(
                {
                    "status": "success",
                    "dtoken": "token",
                    "duration": 120,
                    "filename": "lecture.m4a",
                }
            )
        ]
    )

    metadata = VideoProgressClient(session=cast(requests.Session, session)).fetch_metadata(
        fixture_task(),
        fid="fid-1",
        kind=MediaKind.VIDEO,
    )

    assert metadata.kind is MediaKind.AUDIO


def test_media_metadata_and_http_failures_do_not_render_tokens_or_response_data() -> None:
    metadata = MediaMetadata(dtoken="private-dtoken", duration_seconds=120)
    assert "private-dtoken" not in repr(metadata)

    denied = response({"detail": "private-response-body"}, 403)
    denied.url = "https://mooc1.chaoxing.com/status/private-url-token"
    session = StubSession([denied])

    with pytest.raises(PlatformHTTPError) as raised:
        VideoProgressClient(session=cast(requests.Session, session)).fetch_metadata(
            fixture_task(),
            fid="private-fid",
            kind=MediaKind.VIDEO,
        )

    rendered = f"{raised.value!r} {raised.value}"
    for secret in ("private-response-body", "private-url-token", "private-fid"):
        assert secret not in rendered


def test_reports_progress_and_includes_optional_attention_fields() -> None:
    session = StubSession([response({"isPassed": True})])
    client = VideoProgressClient(
        session=cast(requests.Session, session),
        timestamp_ms=lambda: 1_700_000_000_000,
    )
    task = fixture_task(
        attention_duration="30",
        attention_duration_enc="attention-enc",
        face_capture_enc="face-enc",
    )

    result = client.report_progress(
        course=fixture_course(),
        task=task,
        metadata=MediaMetadata(dtoken="dtoken-1", duration_seconds=120),
        user_id="user-42",
        playing_time_seconds=30,
        kind=MediaKind.VIDEO,
    )

    assert result.is_passed is True
    assert result.rt == "0.9"
    params = session.calls[0][2]["params"]
    assert params["enc"] == "b930b1e3c5bff0de8e097c682df119ed"
    assert params["videoFaceCaptureEnc"] == "face-enc"
    assert params["attDuration"] == "30"
    assert params["attDurationEnc"] == "attention-enc"
    assert params["_t"] == 1_700_000_000_000
    assert session.calls[0][2]["verify"] is True


def test_unknown_rt_retries_only_for_403_and_tls_cannot_be_disabled() -> None:
    session = StubSession([response({}, 403), response({"isPassed": False})])
    client = VideoProgressClient(session=cast(requests.Session, session))
    result = client.report_progress(
        course=fixture_course(),
        task=fixture_task(other_info="node"),
        metadata=MediaMetadata(dtoken="token", duration_seconds=120),
        user_id="user-42",
        playing_time_seconds=0,
        kind=MediaKind.AUDIO,
    )
    assert result.rt == "1"
    assert [call[2]["params"]["rt"] for call in session.calls] == ["0.9", "1"]

    forbidden = StubSession([response({}, 403), response({}, 403)])
    with pytest.raises(PlatformHTTPError, match="HTTP 403"):
        VideoProgressClient(session=cast(requests.Session, forbidden)).report_progress(
            course=fixture_course(),
            task=fixture_task(other_info="node"),
            metadata=MediaMetadata(dtoken="token", duration_seconds=120),
            user_id="user-42",
            playing_time_seconds=0,
            kind=MediaKind.VIDEO,
        )
    with pytest.raises(PlatformConfigurationError):
        VideoProgressClient(session=cast(requests.Session, session), tls_verify=False)


def test_captcha_challenge_is_solved_then_the_same_progress_is_retried() -> None:
    challenge = response({})
    challenge.headers["Content-Type"] = "text/html"
    challenge._content = b'<img src="/processVerifyPng.ac">'
    session = StubSession([challenge, response({"isPassed": True})])
    solver = StubCaptchaSolver()
    client = VideoProgressClient(
        session=cast(requests.Session, session),
        captcha_solver=solver,
    )

    result = client.report_progress(
        course=fixture_course(),
        task=fixture_task(),
        metadata=MediaMetadata(dtoken="token", duration_seconds=120),
        user_id="user-42",
        playing_time_seconds=30,
        kind=MediaKind.VIDEO,
    )

    assert result.is_passed is True
    assert solver.calls == 1
    assert len(session.calls) == 2
    assert session.calls[0][2]["params"] == session.calls[1][2]["params"]


def test_persistent_captcha_challenge_stops_after_the_configured_retry() -> None:
    first = response({})
    first.headers["Content-Type"] = "text/html"
    first._content = b'<img src="/processVerifyPng.ac">'
    second = response({})
    second.headers["Content-Type"] = "text/html"
    second._content = b'<img src="/processVerifyPng.ac">'
    session = StubSession([first, second])
    solver = StubCaptchaSolver()

    with pytest.raises(PlatformCaptchaError, match="challenge persisted"):
        VideoProgressClient(
            session=cast(requests.Session, session),
            captcha_solver=solver,
        ).report_progress(
            course=fixture_course(),
            task=fixture_task(),
            metadata=MediaMetadata(dtoken="token", duration_seconds=120),
            user_id="user-42",
            playing_time_seconds=30,
            kind=MediaKind.VIDEO,
        )

    assert solver.calls == 1
    assert len(session.calls) == 2
