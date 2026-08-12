from __future__ import annotations

import json
from collections import deque
from typing import Any, cast

import pytest
import requests

from chaoxing_app.platform.errors import (
    PlatformAuthenticationError,
    PlatformConfigurationError,
    PlatformHTTPError,
    PlatformParseError,
    PlatformTimeoutError,
)
from chaoxing_app.platform.models import Chapter, Course
from chaoxing_app.platform.task_points.cards import DocumentTaskPoint, ReadTaskPoint
from chaoxing_app.platform.task_points.document import (
    DocumentCompletionResult,
    DocumentTaskClient,
    parse_document_knowledge_id,
)
from chaoxing_app.platform.task_points.empty_page import (
    EmptyPageCompletionResult,
    EmptyPageTaskClient,
)
from chaoxing_app.platform.task_points.reading import (
    ReadingCompletionResult,
    ReadingTaskClient,
)


def json_response(
    payload: object,
    *,
    status_code: int = 200,
    url: str = "https://sanitized.example.test/result",
) -> requests.Response:
    result = requests.Response()
    result.status_code = status_code
    result.url = url
    result.encoding = "utf-8"
    result._content = json.dumps(payload).encode("utf-8")
    return result


def text_response(
    payload: str,
    *,
    status_code: int = 200,
    url: str = "https://sanitized.example.test/result",
) -> requests.Response:
    result = requests.Response()
    result.status_code = status_code
    result.url = url
    result.encoding = "utf-8"
    result._content = payload.encode("utf-8")
    return result


class StubSession:
    def __init__(self, outcomes: list[requests.Response | requests.RequestException]) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, requests.RequestException):
            raise outcome
        return outcome


def course() -> Course:
    return Course("course-100", "class-100", "cpi-100", "Fixture Course")


def chapter() -> Chapter:
    return Chapter("chapter-100", "Fixture Chapter", 0, False, False)


def document_task(**changes: str) -> DocumentTaskPoint:
    values = {
        "job_id": "document-job",
        "object_id": "document-object",
        "other_info": "nodeId_42-document",
        "jtoken": "document-token",
    }
    values.update(changes)
    return DocumentTaskPoint(**values)


def reading_task(**changes: str) -> ReadTaskPoint:
    values = {
        "job_id": "reading-job",
        "item_id": "reading-item",
        "title": "Fixture Reading",
        "other_info": "reading-info",
        "jtoken": "reading-token",
    }
    values.update(changes)
    return ReadTaskPoint(**values)


def test_document_node_id_parser_handles_delimited_and_terminal_values() -> None:
    assert parse_document_knowledge_id("nodeId_42-document") == "42"
    assert parse_document_knowledge_id("prefix-nodeId_84") == "84"

    with pytest.raises(PlatformParseError, match="missing nodeId value") as raised:
        parse_document_knowledge_id("private malformed task metadata")
    assert "private malformed task metadata" not in str(raised.value)


def test_document_completion_uses_typed_protocol_and_tls_verification() -> None:
    session = StubSession([text_response("accepted")])
    client = DocumentTaskClient(
        session=cast(requests.Session, session),
        timeout=(2.0, 9.0),
        timestamp_ms=lambda: 1_700_000_000_000,
    )

    result = client.complete(course(), document_task())

    assert result == DocumentCompletionResult(True, 200, "42")
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == "https://mooc1.chaoxing.com/ananas/job/document"
    assert kwargs["params"] == {
        "jobid": "document-job",
        "knowledgeid": "42",
        "courseid": "course-100",
        "clazzid": "class-100",
        "jtoken": "document-token",
        "_dc": 1_700_000_000_000,
    }
    assert kwargs["timeout"] == (2.0, 9.0)
    assert kwargs["verify"] is True


def test_reading_completion_covers_readv2_parameters_and_status() -> None:
    session = StubSession([json_response({"status": True, "msg": " completed "})])
    client = ReadingTaskClient(session=cast(requests.Session, session))

    result = client.complete(course(), reading_task(), knowledge_id="chapter-100")

    assert result == ReadingCompletionResult(True, 200, "completed")
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == "https://mooc1.chaoxing.com/ananas/job/readv2"
    assert kwargs["params"] == {
        "jobid": "reading-job",
        "knowledgeid": "chapter-100",
        "jtoken": "reading-token",
        "courseid": "course-100",
        "clazzid": "class-100",
    }
    assert kwargs["verify"] is True

    rejected_session = StubSession([json_response({"status": False, "msg": "not accepted"})])
    rejected = ReadingTaskClient(session=cast(requests.Session, rejected_session)).complete(
        course(), reading_task(), knowledge_id="chapter-100"
    )
    assert rejected.accepted is False


def test_empty_page_completion_covers_studentstudyajax_parameters() -> None:
    session = StubSession([text_response("chapter opened")])
    client = EmptyPageTaskClient(session=cast(requests.Session, session))

    result = client.complete(course(), chapter())

    assert result == EmptyPageCompletionResult(True, 200, "chapter-100")
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/studentstudyAjax")
    assert kwargs["params"] == {
        "courseId": "course-100",
        "clazzid": "class-100",
        "chapterId": "chapter-100",
        "cpi": "cpi-100",
        "verificationcode": "",
        "mooc2": 1,
        "microTopicId": 0,
        "editorPreview": 0,
    }
    assert kwargs["verify"] is True


def test_login_redirect_is_rejected_without_exposing_response_content() -> None:
    session = StubSession(
        [
            text_response(
                "private login response",
                url="https://passport2.chaoxing.com/login",
            )
        ]
    )
    client = DocumentTaskClient(session=cast(requests.Session, session))

    with pytest.raises(PlatformAuthenticationError, match="not authenticated") as raised:
        client.complete(course(), document_task())
    assert "private login response" not in str(raised.value)


def test_login_page_marker_is_rejected_for_empty_page() -> None:
    session = StubSession([text_response("\u7528\u6237\u767b\u5f55 private login response")])
    client = EmptyPageTaskClient(session=cast(requests.Session, session))

    with pytest.raises(PlatformAuthenticationError, match="not authenticated"):
        client.complete(course(), chapter())


def test_http_timeout_parse_and_configuration_errors_are_explicit() -> None:
    failed = StubSession([text_response("private failure", status_code=503)])
    with pytest.raises(PlatformHTTPError, match="HTTP 503") as raised:
        EmptyPageTaskClient(session=cast(requests.Session, failed)).complete(course(), chapter())
    assert "private failure" not in str(raised.value)

    timed_out = StubSession([requests.Timeout("private timeout detail")])
    with pytest.raises(PlatformTimeoutError, match="request timed out") as raised:
        DocumentTaskClient(session=cast(requests.Session, timed_out)).complete(
            course(), document_task()
        )
    assert "private timeout detail" not in str(raised.value)

    malformed = StubSession([text_response("private non-json payload")])
    with pytest.raises(PlatformParseError, match="not valid JSON") as raised:
        ReadingTaskClient(session=cast(requests.Session, malformed)).complete(
            course(), reading_task(), knowledge_id="chapter-100"
        )
    assert "private non-json payload" not in str(raised.value)

    session = StubSession([])
    with pytest.raises(PlatformConfigurationError, match="TLS verification"):
        ReadingTaskClient(session=cast(requests.Session, session), tls_verify=False)
