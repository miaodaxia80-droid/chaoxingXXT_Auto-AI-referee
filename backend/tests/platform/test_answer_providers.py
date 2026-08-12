from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from typing import Any, cast

import pytest
import requests

from chaoxing_app.platform.answer_providers import (
    LikeAnswerProvider,
    OpenAICompatibleAnswerProvider,
    SiliconFlowAnswerProvider,
    TikuAdapterAnswerProvider,
    YanxiAnswerProvider,
)
from chaoxing_app.platform.errors import PlatformConfigurationError
from chaoxing_app.platform.task_points.quiz import (
    AnswerProvider,
    AnswerProviderCapability,
    ProviderAnswer,
    QuizQuestion,
    QuizQuestionType,
)


def response(payload: object, *, status_code: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status_code
    result.url = "https://provider.invalid/redacted"
    result._content = json.dumps(payload).encode("utf-8")
    result.headers["Content-Type"] = "application/json"
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


class ExplodingSession:
    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        raise RuntimeError(f"request leaked {method} {url} {kwargs!r}")


def multiple_question() -> QuizQuestion:
    return QuizQuestion(
        question_id="q-multiple",
        title="Select secure protocols",
        question_type=QuizQuestionType.MULTIPLE,
        type_code="1",
        options=("A. HTTPS", "B. Telnet", "C. SSH"),
    )


def judgement_question() -> QuizQuestion:
    return QuizQuestion(
        question_id="q-judgement",
        title="TLS certificate verification should remain enabled.",
        question_type=QuizQuestionType.JUDGEMENT,
        type_code="3",
    )


def assert_answer(
    value: ProviderAnswer | str | tuple[str, ...] | None,
    *,
    expected: str | tuple[str, ...],
    source: str,
) -> None:
    assert isinstance(value, ProviderAnswer)
    assert value.value == expected
    assert value.source == source


def as_protocol(provider: AnswerProvider) -> AnswerProvider:
    return provider


def test_yanxi_request_contract_rotates_only_an_exhausted_token() -> None:
    session = StubSession(
        [
            response({"code": 0, "data": {"answer": "token \u6b21\u6570\u4e0d\u8db3"}}),
            response({"code": 1, "data": {"answer": "HTTPS"}}),
        ]
    )
    provider = YanxiAnswerProvider(
        tokens=("spent-token", "working-token"),
        session=cast(requests.Session, session),
        timeout=(1.5, 7.0),
    )

    answer = as_protocol(provider).answer(multiple_question(), course_context="Security")

    assert_answer(answer, expected="HTTPS", source="yanxi")
    assert provider.capability is AnswerProviderCapability.YANXI
    assert [call[0] for call in session.calls] == ["GET", "GET"]
    assert [call[2]["params"] for call in session.calls] == [
        {"question": "Select secure protocols", "token": "spent-token"},
        {"question": "Select secure protocols", "token": "working-token"},
    ]
    assert all(call[2]["timeout"] == (1.5, 7.0) for call in session.calls)
    assert all(call[2]["verify"] is True for call in session.calls)
    assert all(call[2]["allow_redirects"] is False for call in session.calls)


def test_like_request_and_typed_choice_response_contract() -> None:
    session = StubSession([response({"data": {"type": 1, "choose": "AC"}})])
    provider = LikeAnswerProvider(
        token="like-key",
        model="knowledge-model",
        search=True,
        session=cast(requests.Session, session),
        timeout=(2.0, 8.0),
    )

    answer = as_protocol(provider).answer(multiple_question())

    assert_answer(answer, expected=("A. HTTPS", "C. SSH"), source="like")
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://api.datam.site/search"
    assert kwargs["json"] == {
        "query": (
            "\u3010\u591a\u9009\u9898\u3011Select secure protocols\n"
            "A. HTTPS, B. Telnet, C. SSH"
        ),
        "token": "like-key",
        "model": "knowledge-model",
        "search": True,
    }
    assert kwargs["timeout"] == (2.0, 8.0)
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False


def test_tiku_adapter_request_and_best_answer_contract() -> None:
    session = StubSession(
        [response({"plat": 0, "answer": {"bestAnswer": ["HTTPS", "SSH"]}})]
    )
    provider = TikuAdapterAnswerProvider(
        endpoint="https://adapter.example.test/v1/query",
        session=cast(requests.Session, session),
        timeout=(2.5, 9.0),
    )

    answer = as_protocol(provider).answer(multiple_question())

    assert_answer(answer, expected=("HTTPS", "SSH"), source="tiku_adapter")
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://adapter.example.test/v1/query"
    assert kwargs["json"] == {
        "question": "Select secure protocols",
        "options": ["HTTPS", "Telnet", "SSH"],
        "type": 1,
    }
    assert kwargs["timeout"] == (2.5, 9.0)
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False


def test_openai_compatible_chat_completion_contract_and_strict_json_response() -> None:
    session = StubSession(
        [
            response(
                {
                    "choices": [
                        {"message": {"content": '```json\n{"Answer": ["C. SSH"]}\n```'}}
                    ]
                }
            )
        ]
    )
    provider = OpenAICompatibleAnswerProvider(
        base_url="https://llm.example.test/v1",
        model="quiz-model",
        api_key="openai-secret",
        session=cast(requests.Session, session),
        timeout=(3.0, 12.0),
    )

    answer = as_protocol(provider).answer(
        QuizQuestion(
            question_id="q-single",
            title="Which protocol provides secure shell access?",
            question_type=QuizQuestionType.SINGLE,
            type_code="0",
            options=("A. FTP", "B. Telnet", "C. SSH"),
        ),
        course_context="Network Security",
    )

    assert_answer(answer, expected="C. SSH", source="openai_compatible")
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://llm.example.test/v1/chat/completions"
    assert kwargs["headers"] == {
        "Authorization": "Bearer openai-secret",
        "Content-Type": "application/json",
    }
    assert kwargs["json"]["model"] == "quiz-model"
    assert kwargs["json"]["stream"] is False
    assert kwargs["json"]["temperature"] == 0
    assert kwargs["json"]["response_format"] == {"type": "json_object"}
    assert "Never guess" in kwargs["json"]["messages"][0]["content"]
    assert "Course context: Network Security" in kwargs["json"]["messages"][1]["content"]
    assert kwargs["timeout"] == (3.0, 12.0)
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False


def test_siliconflow_reuses_chat_contract_with_an_independent_identity() -> None:
    session = StubSession(
        [response({"choices": [{"message": {"content": '{"Answer": ["true"]}'}}]})]
    )
    provider = SiliconFlowAnswerProvider(
        api_key="silicon-secret",
        session=cast(requests.Session, session),
        timeout=(3.5, 15.0),
    )

    answer = as_protocol(provider).answer(judgement_question())

    assert_answer(answer, expected="true", source="siliconflow")
    assert provider.capability is AnswerProviderCapability.SILICONFLOW_ENSEMBLE
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://api.siliconflow.cn/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer silicon-secret"
    assert kwargs["json"]["model"] == "deepseek-ai/DeepSeek-V3"
    assert kwargs["timeout"] == (3.5, 15.0)
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False


ProviderFactory = Callable[[requests.Session], AnswerProvider]


@pytest.mark.parametrize(
    ("provider_name", "secret", "factory"),
    [
        (
            "yanxi",
            "yanxi-secret",
            lambda session: YanxiAnswerProvider(
                tokens="yanxi-secret",
                endpoint="https://endpoint-secret.example.test/yanxi",
                session=session,
            ),
        ),
        (
            "like",
            "like-secret",
            lambda session: LikeAnswerProvider(
                token="like-secret",
                endpoint="https://endpoint-secret.example.test/like",
                session=session,
            ),
        ),
        (
            "tiku_adapter",
            "endpoint-secret",
            lambda session: TikuAdapterAnswerProvider(
                endpoint="https://endpoint-secret.example.test/adapter",
                session=session,
            ),
        ),
        (
            "openai_compatible",
            "openai-secret",
            lambda session: OpenAICompatibleAnswerProvider(
                base_url="https://endpoint-secret.example.test/v1",
                model="model-secret",
                api_key="openai-secret",
                session=session,
            ),
        ),
        (
            "siliconflow",
            "silicon-secret",
            lambda session: SiliconFlowAnswerProvider(
                api_key="silicon-secret",
                base_url="https://endpoint-secret.example.test/v1",
                session=session,
            ),
        ),
    ],
)
def test_provider_transport_errors_are_safe_and_repr_is_redacted(
    provider_name: str,
    secret: str,
    factory: ProviderFactory,
) -> None:
    provider = factory(cast(requests.Session, ExplodingSession()))

    assert provider.answer(multiple_question()) is None
    rendered = repr(provider)
    assert provider_name.split("_")[0].casefold() in rendered.casefold()
    assert secret not in rendered
    assert "endpoint-secret" not in rendered
    assert "model-secret" not in rendered


@pytest.mark.parametrize(
    "factory",
    [
        lambda session: YanxiAnswerProvider(tokens="key", session=session),
        lambda session: LikeAnswerProvider(token="key", session=session),
        lambda session: TikuAdapterAnswerProvider(
            endpoint="https://adapter.example.test/query",
            session=session,
        ),
        lambda session: OpenAICompatibleAnswerProvider(
            base_url="https://llm.example.test/v1",
            model="model",
            api_key="key",
            session=session,
        ),
        lambda session: SiliconFlowAnswerProvider(api_key="key", session=session),
    ],
)
def test_provider_non_200_or_invalid_json_never_produces_an_answer(
    factory: ProviderFactory,
) -> None:
    failed = StubSession([response({"answer": "A"}, status_code=503)])
    invalid = StubSession([ValueError("invalid JSON with api-key-secret")])

    assert factory(cast(requests.Session, failed)).answer(multiple_question()) is None
    assert factory(cast(requests.Session, invalid)).answer(multiple_question()) is None


@pytest.mark.parametrize(
    "factory",
    [
        lambda: YanxiAnswerProvider(tokens="key", tls_verify=False),
        lambda: LikeAnswerProvider(token="key", tls_verify=False),
        lambda: TikuAdapterAnswerProvider(
            endpoint="https://adapter.example.test/query",
            tls_verify=False,
        ),
        lambda: OpenAICompatibleAnswerProvider(
            base_url="https://llm.example.test/v1",
            model="model",
            api_key="key",
            tls_verify=False,
        ),
        lambda: SiliconFlowAnswerProvider(api_key="key", tls_verify=False),
    ],
)
def test_tls_verification_cannot_be_disabled(factory: Callable[[], AnswerProvider]) -> None:
    with pytest.raises(PlatformConfigurationError, match="cannot be disabled"):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: YanxiAnswerProvider(tokens="key", endpoint="http://providers.example.test"),
        lambda: LikeAnswerProvider(token="key", endpoint="http://providers.example.test"),
        lambda: TikuAdapterAnswerProvider(endpoint="http://providers.example.test"),
        lambda: OpenAICompatibleAnswerProvider(
            base_url="http://providers.example.test/v1",
            model="model",
            api_key="key",
        ),
        lambda: SiliconFlowAnswerProvider(
            api_key="key",
            base_url="http://providers.example.test/v1",
        ),
    ],
)
def test_all_providers_require_https_by_default(factory: Callable[[], AnswerProvider]) -> None:
    with pytest.raises(PlatformConfigurationError, match="not allowed"):
        factory()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://localhost/query",
        "https://service.local/query",
        "https://127.0.0.1/query",
        "https://10.10.0.1/query",
        "https://169.254.10.1/query",
        "https://[::1]/query",
        "https://[fe80::1]/query",
    ],
)
def test_local_and_private_endpoints_are_rejected_by_default(endpoint: str) -> None:
    with pytest.raises(PlatformConfigurationError, match="not allowed"):
        TikuAdapterAnswerProvider(endpoint=endpoint)


@pytest.mark.parametrize(
    "factory",
    [
        lambda session: YanxiAnswerProvider(
            tokens="key",
            endpoint="http://127.0.0.1:8080/yanxi",
            session=session,
            allow_unsafe_endpoint=True,
        ),
        lambda session: LikeAnswerProvider(
            token="key",
            endpoint="http://127.0.0.1:8080/like",
            session=session,
            allow_unsafe_endpoint=True,
        ),
        lambda session: TikuAdapterAnswerProvider(
            endpoint="http://127.0.0.1:8080/adapter",
            session=session,
            allow_unsafe_endpoint=True,
        ),
        lambda session: OpenAICompatibleAnswerProvider(
            base_url="http://127.0.0.1:8080/v1",
            model="model",
            api_key="key",
            session=session,
            allow_unsafe_endpoint=True,
        ),
        lambda session: SiliconFlowAnswerProvider(
            api_key="key",
            base_url="http://127.0.0.1:8080/v1",
            session=session,
            allow_unsafe_endpoint=True,
        ),
    ],
)
def test_all_providers_require_explicit_opt_in_for_local_endpoints(
    factory: ProviderFactory,
) -> None:
    provider = factory(cast(requests.Session, ExplodingSession()))

    assert provider.answer(multiple_question()) is None


@pytest.mark.parametrize(
    "timeout",
    [
        ("5", 10.0),
        (True, 10.0),
        (float("nan"), 10.0),
        (float("inf"), 10.0),
        (5.0, float("-inf")),
        (0.0, 10.0),
    ],
)
def test_timeouts_must_be_finite_positive_numbers(timeout: object) -> None:
    with pytest.raises(PlatformConfigurationError, match="timeouts"):
        TikuAdapterAnswerProvider(
            endpoint="https://adapter.example.test/query",
            timeout=cast(tuple[float, float], timeout),
        )


@pytest.mark.parametrize(
    ("provider", "question"),
    [
        (
            YanxiAnswerProvider(
                tokens="key",
                session=cast(
                    requests.Session,
                    StubSession([response({"code": 1, "data": {"answer": []}})]),
                ),
            ),
            multiple_question(),
        ),
        (
            LikeAnswerProvider(
                token="key",
                session=cast(
                    requests.Session,
                    StubSession([response({"data": {"type": 3, "judge": 2}})]),
                ),
            ),
            judgement_question(),
        ),
        (
            TikuAdapterAnswerProvider(
                endpoint="https://adapter.example.test/query",
                session=cast(
                    requests.Session,
                    StubSession([response({"answer": {"bestAnswer": []}})]),
                ),
            ),
            multiple_question(),
        ),
        (
            OpenAICompatibleAnswerProvider(
                base_url="https://llm.example.test/v1",
                model="model",
                api_key="key",
                session=cast(
                    requests.Session,
                    StubSession(
                        [
                            response(
                                {
                                    "choices": [
                                        {"message": {"content": '{"Answer": "A"}'}}
                                    ]
                                }
                            )
                        ]
                    ),
                ),
            ),
            multiple_question(),
        ),
        (
            SiliconFlowAnswerProvider(
                api_key="key",
                session=cast(
                    requests.Session,
                    StubSession([response({"choices": [{"message": {}}]})]),
                ),
            ),
            judgement_question(),
        ),
    ],
)
def test_malformed_or_empty_provider_payloads_are_never_guessed(
    provider: AnswerProvider,
    question: QuizQuestion,
) -> None:
    assert provider.answer(question) is None


def test_configuration_errors_do_not_echo_an_invalid_endpoint() -> None:
    with pytest.raises(PlatformConfigurationError) as raised:
        OpenAICompatibleAnswerProvider(
            base_url="invalid-endpoint-secret",
            model="model-secret",
            api_key="api-key-secret",
        )

    rendered = str(raised.value)
    assert "endpoint-secret" not in rendered
    assert "model-secret" not in rendered
    assert "api-key-secret" not in rendered
