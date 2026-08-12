from __future__ import annotations

from collections import deque
from typing import Any, cast

import requests

from chaoxing_app.platform.answer_providers.web_search import DuckDuckGoSearchContext
from chaoxing_app.platform.task_points.quiz import QuizQuestion, QuizQuestionType


def response(html: str, *, status_code: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status_code
    result.url = "https://html.duckduckgo.com/html/"
    result._content = html.encode()
    result.headers["Content-Type"] = "text/html; charset=utf-8"
    return result


class StubSession:
    def __init__(self, outcomes: list[requests.Response | Exception]) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def question() -> QuizQuestion:
    return QuizQuestion(
        question_id="search-question",
        title="What is transport layer security?",
        question_type=QuizQuestionType.SHORT_ANSWER,
        type_code="4",
    )


def test_duckduckgo_search_returns_bounded_untrusted_excerpts() -> None:
    session = StubSession(
        [
            response(
                """
                <div class="result">
                  <div class="result__title"><a>TLS reference</a></div>
                  <div class="result__snippet">Encrypted transport facts.</div>
                </div>
                """
            )
        ]
    )
    search = DuckDuckGoSearchContext(session=cast(requests.Session, session))

    context = search(question(), "Network Security")

    assert "Untrusted web search excerpts" in context
    assert "TLS reference: Encrypted transport facts." in context
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == "https://html.duckduckgo.com/html/"
    assert kwargs["params"] == {
        "q": "Network Security What is transport layer security?"
    }
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False


def test_duckduckgo_search_failure_returns_empty_context() -> None:
    session = StubSession([requests.Timeout("offline")])
    search = DuckDuckGoSearchContext(session=cast(requests.Session, session))

    assert search(question(), "Network Security") == ""
