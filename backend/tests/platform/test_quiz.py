from __future__ import annotations

import json
from collections import deque
from typing import Any, cast

import pytest
import requests

from chaoxing_app.platform.errors import PlatformParseError
from chaoxing_app.platform.models import Course
from chaoxing_app.platform.task_points.cards import JobDefaults, QuizTaskPoint
from chaoxing_app.platform.task_points.quiz import (
    SUPPORTED_PROVIDER_CAPABILITIES,
    AnswerProviderCapability,
    QuizAnswerResolution,
    QuizQuestion,
    QuizQuestionType,
    QuizSubmissionMode,
    QuizSubmissionStatus,
    QuizTaskClient,
    build_quiz_fetch_params,
    parse_quiz_form,
)

QUIZ_HTML = """
<html>
  <body>
    <form id="form1">
      <input name="workAnswerId" value="answer-record-1">
      <input name="oldWorkId" value="old-work-1">
      <input name="answer999" value="stale-answer">
      <div class="singleQuesId" data="101">
        <div class="TiMu" data="0"></div>
        <div class="Zy_TItle">Which protocol is used?</div>
        <ul>
          <li aria-label="A. HTTP"></li>
          <li aria-label="B. SMTP"></li>
        </ul>
      </div>
      <div class="singleQuesId" data="102">
        <div class="TiMu" data="3"></div>
        <div class="Zy_TItle">TLS verification should stay enabled.</div>
      </div>
    </form>
  </body>
</html>
"""


def response(
    content: str | object,
    *,
    status_code: int = 200,
    url: str = "https://sanitized.example.test/quiz",
) -> requests.Response:
    result = requests.Response()
    result.status_code = status_code
    result.url = url
    result.encoding = "utf-8"
    if isinstance(content, str):
        result._content = content.encode("utf-8")
    else:
        result._content = json.dumps(content).encode("utf-8")
        result.headers["Content-Type"] = "application/json"
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


class FixtureProvider:
    configured = True

    def __init__(self, answers: dict[str, str | None]) -> None:
        self.answers = answers
        self.contexts: list[str] = []

    def answer(self, question: QuizQuestion, *, course_context: str = "") -> str | None:
        self.contexts.append(course_context)
        return self.answers[question.question_id]


def course() -> Course:
    return Course("course-100", "class-100", "course-cpi", "Network Security")


def quiz_task() -> QuizTaskPoint:
    return QuizTaskPoint(
        job_id="work-quiz-100",
        other_info="nodeId_chapter-100",
        mid="quiz-mid",
        enc="quiz-enc",
        aid="quiz-aid",
    )


def defaults() -> JobDefaults:
    return JobDefaults(
        ktoken="quiz-ktoken",
        cpi="defaults-cpi",
        knowledge_id="chapter-100",
    )


def test_parses_typed_html_questions_and_preserves_non_answer_form_fields() -> None:
    form = parse_quiz_form(QUIZ_HTML)

    assert form.fields == {
        "workAnswerId": "answer-record-1",
        "oldWorkId": "old-work-1",
    }
    assert form.answerwqbid == "101,102,"
    assert [question.question_type for question in form.questions] == [
        QuizQuestionType.SINGLE,
        QuizQuestionType.JUDGEMENT,
    ]
    assert form.questions[0].options == ("A. HTTP", "B. SMTP")


def test_json_question_payload_is_supported_without_exposing_raw_parse_failures() -> None:
    form = parse_quiz_form(
        json.dumps(
            {
                "formData": {"workId": "work-1"},
                "questions": [
                    {
                        "id": 7,
                        "title": "Choose both",
                        "typeCode": "1",
                        "options": {"A": "First", "B": "Second"},
                    }
                ],
            }
        )
    )

    assert form.fields == {"workId": "work-1"}
    assert form.questions[0].question_type is QuizQuestionType.MULTIPLE
    assert form.questions[0].options == ("A. First", "B. Second")

    with pytest.raises(PlatformParseError, match="not valid JSON") as raised:
        parse_quiz_form('{"private-token": invalid}')
    assert "private-token" not in str(raised.value)


def test_fetch_parameters_match_workid_protocol() -> None:
    assert build_quiz_fetch_params(
        course(),
        quiz_task(),
        knowledge_id="chapter-100",
        ktoken="quiz-ktoken",
        cpi="defaults-cpi",
    ) == {
        "api": "1",
        "workId": "quiz-100",
        "jobid": "work-quiz-100",
        "originJobId": "work-quiz-100",
        "needRedirect": "true",
        "skipHeader": "true",
        "knowledgeid": "chapter-100",
        "ktoken": "quiz-ktoken",
        "cpi": "defaults-cpi",
        "ut": "s",
        "clazzId": "class-100",
        "type": "",
        "enc": "quiz-enc",
        "mooc2": "1",
        "courseid": "course-100",
    }


def test_complete_submits_only_normalized_provider_answers_at_threshold() -> None:
    session = StubSession([response(QUIZ_HTML), response({"status": True, "msg": "ok"})])
    provider = FixtureProvider({"101": "HTTP", "102": "correct"})
    client = QuizTaskClient(
        session=cast(requests.Session, session),
        provider=provider,
        submit_threshold=1.0,
    )

    result = client.complete(course(), quiz_task(), defaults=defaults())

    assert result.status is QuizSubmissionStatus.SUBMITTED
    assert result.submitted is True
    assert result.coverage == 1.0
    assert provider.contexts == ["Network Security", "Network Security"]
    assert len(session.calls) == 2
    fetch_method, fetch_url, fetch_kwargs = session.calls[0]
    assert fetch_method == "GET"
    assert fetch_url.endswith("/mooc-ans/api/work")
    assert fetch_kwargs["params"]["knowledgeid"] == "chapter-100"
    assert fetch_kwargs["params"]["ktoken"] == "quiz-ktoken"
    submit_method, submit_url, submit_kwargs = session.calls[1]
    assert submit_method == "POST"
    assert submit_url.endswith("/work/addStudentWorkNew")
    assert submit_kwargs["data"] == {
        "workAnswerId": "answer-record-1",
        "oldWorkId": "old-work-1",
        "answerwqbid": "101,102,",
        "pyFlag": "",
        "answer101": "A",
        "answertype101": "0",
        "answer102": "true",
        "answertype102": "3",
    }
    assert submit_kwargs["verify"] is True


def test_unconfigured_or_empty_provider_never_posts_or_guesses() -> None:
    unconfigured_session = StubSession([response(QUIZ_HTML)])
    unconfigured = QuizTaskClient(
        session=cast(requests.Session, unconfigured_session),
    ).complete(course(), quiz_task(), defaults=defaults())

    assert unconfigured.status is QuizSubmissionStatus.UNSUBMITTED
    assert unconfigured.reason == "provider_unconfigured"
    assert unconfigured.needs_attention is True
    assert unconfigured.answered_count == 0
    assert len(unconfigured_session.calls) == 1

    empty_session = StubSession([response(QUIZ_HTML)])
    empty_provider = FixtureProvider({"101": None, "102": None})
    empty = QuizTaskClient(
        session=cast(requests.Session, empty_session),
        provider=empty_provider,
    ).complete(course(), quiz_task(), defaults=defaults())

    assert empty.status is QuizSubmissionStatus.UNSUBMITTED
    assert empty.reason == "no_answers"
    assert empty.coverage == 0.0
    assert len(empty_session.calls) == 1


def test_low_coverage_saves_known_answers_but_remains_unsubmitted() -> None:
    session = StubSession([response(QUIZ_HTML), response({"status": True})])
    provider = FixtureProvider({"101": "A", "102": None})
    result = QuizTaskClient(
        session=cast(requests.Session, session),
        provider=provider,
        submit_threshold=1.0,
    ).complete(course(), quiz_task(), defaults=defaults())

    assert result.status is QuizSubmissionStatus.UNSUBMITTED
    assert result.submitted is False
    assert result.saved is True
    assert result.needs_attention is True
    assert result.reason == "coverage_below_threshold"
    assert result.coverage == 0.5
    assert session.calls[1][2]["data"]["pyFlag"] == "1"
    assert session.calls[1][2]["data"]["answer101"] == "A"
    assert session.calls[1][2]["data"]["answer102"] == ""


def test_save_only_never_turns_full_coverage_into_a_submission() -> None:
    session = StubSession([response(QUIZ_HTML), response({"status": "true"})])
    provider = FixtureProvider({"101": "A", "102": "true"})
    result = QuizTaskClient(
        session=cast(requests.Session, session),
        provider=provider,
        mode=QuizSubmissionMode.SAVE_ONLY,
    ).complete(course(), quiz_task(), defaults=defaults())

    assert result.status is QuizSubmissionStatus.SAVED
    assert result.saved is True
    assert result.submitted is False
    assert result.reason == "save_only"
    assert session.calls[1][2]["data"]["pyFlag"] == "1"


def test_provider_exception_is_redacted_and_does_not_trigger_submission() -> None:
    class FailingProvider:
        configured = True

        def answer(self, question: QuizQuestion, *, course_context: str = "") -> str:
            del question, course_context
            raise RuntimeError("provider-token=private-secret")

    session = StubSession([response(QUIZ_HTML)])
    result = QuizTaskClient(
        session=cast(requests.Session, session),
        provider=FailingProvider(),
    ).complete(course(), quiz_task(), defaults=defaults())

    assert result.status is QuizSubmissionStatus.UNSUBMITTED
    assert result.reason == "provider_error"
    assert result.provider_error_count == 2
    assert "private-secret" not in repr(result)
    assert len(session.calls) == 1


def test_public_submit_recomputes_coverage_and_rejects_unknown_answer_ids() -> None:
    session = StubSession([])
    form = parse_quiz_form(QUIZ_HTML)
    forged = QuizAnswerResolution(
        answers={"not-a-question": "A"},
        unanswered_question_ids=(),
        provider_error_count=0,
        provider_configured=True,
        total_questions=1,
    )

    result = QuizTaskClient(session=cast(requests.Session, session)).submit(form, forged)

    assert result.status is QuizSubmissionStatus.UNSUBMITTED
    assert result.reason == "no_answers"
    assert result.coverage == 0.0
    assert not session.calls


def test_five_provider_capabilities_remain_explicit() -> None:
    assert {
        AnswerProviderCapability.YANXI,
        AnswerProviderCapability.LIKE,
        AnswerProviderCapability.TIKU_ADAPTER,
        AnswerProviderCapability.OPENAI_COMPATIBLE,
        AnswerProviderCapability.SILICONFLOW_ENSEMBLE,
    } == SUPPORTED_PROVIDER_CAPABILITIES
