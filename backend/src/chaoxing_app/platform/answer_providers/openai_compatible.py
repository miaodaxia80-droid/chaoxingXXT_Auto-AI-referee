from __future__ import annotations

import json
import re
from typing import ClassVar, Final

import requests

from chaoxing_app.platform.task_points.quiz import (
    AnswerProviderCapability,
    ProviderAnswer,
    QuizQuestion,
    QuizQuestionType,
)

from ._common import (
    DEFAULT_TIMEOUT,
    JSONHTTPClient,
    RequestTimeout,
    SafeAnswerProvider,
    TLSVerify,
    answer_from_values,
    as_mapping,
    require_nonempty,
    validate_endpoint,
)

_JSON_FENCE = re.compile(r"\A\s*```(?:json)?\s*(.*?)\s*```\s*\Z", re.DOTALL | re.IGNORECASE)
_TYPE_INSTRUCTION: Final[dict[QuizQuestionType, str]] = {
    QuizQuestionType.SINGLE: "Return exactly one option in Answer.",
    QuizQuestionType.MULTIPLE: "Return every correct option in Answer.",
    QuizQuestionType.COMPLETION: "Return the blank values in order in Answer.",
    QuizQuestionType.JUDGEMENT: "Return exactly one of true or false in Answer.",
    QuizQuestionType.SHORT_ANSWER: "Return one concise answer in Answer.",
}


class OpenAICompatibleAnswerProvider(SafeAnswerProvider):
    capability: ClassVar = AnswerProviderCapability.OPENAI_COMPATIBLE
    provider_id: ClassVar = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        session: requests.Session | None = None,
        timeout: RequestTimeout = DEFAULT_TIMEOUT,
        tls_verify: TLSVerify = True,
        allow_unsafe_endpoint: bool = False,
    ) -> None:
        self._model = require_nonempty(model, field="model")
        self._api_key = require_nonempty(api_key, field="API key")
        self._http = JSONHTTPClient(
            endpoint=_chat_completions_url(
                base_url,
                allow_unsafe_endpoint=allow_unsafe_endpoint,
            ),
            session=session,
            timeout=timeout,
            tls_verify=tls_verify,
            allow_unsafe_endpoint=allow_unsafe_endpoint,
        )

    def answer(
        self,
        question: QuizQuestion,
        *,
        course_context: str = "",
    ) -> ProviderAnswer | None:
        payload = self._http.request(
            "POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json_body={
                "model": self._model,
                "messages": _messages(question, course_context),
                "stream": False,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        )
        values = _parse_chat_completion(payload, question)
        return (
            answer_from_values(values, source=self.provider_id)
            if values is not None
            else None
        )


def _chat_completions_url(
    base_url: str,
    *,
    allow_unsafe_endpoint: bool,
) -> str:
    normalized = validate_endpoint(
        base_url,
        allow_unsafe_endpoint=allow_unsafe_endpoint,
    )
    if normalized.casefold().endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _messages(question: QuizQuestion, course_context: str) -> list[dict[str, str]]:
    instruction = _TYPE_INSTRUCTION.get(
        question.question_type,
        "Return one concise answer in Answer.",
    )
    system = (
        "Answer the quiz only when you know the answer. Never guess. "
        "Return only a strict JSON object shaped as {\"Answer\": [\"value\"]}. "
        f"{instruction} If the answer is unavailable or uncertain, return {{\"Answer\": []}}."
    )
    user_parts = [f"Question: {question.title}"]
    if question.options:
        user_parts.append("Options:\n" + "\n".join(question.options))
    if course_context.strip():
        user_parts.append(f"Course context: {course_context.strip()}")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _parse_chat_completion(
    payload: object,
    question: QuizQuestion,
) -> list[str] | None:
    envelope = as_mapping(payload)
    if envelope is None:
        return None
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = as_mapping(choices[0])
    if first is None:
        return None
    message = as_mapping(first.get("message"))
    if message is None:
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    match = _JSON_FENCE.fullmatch(content)
    raw_json = match.group(1) if match is not None else content.strip()
    try:
        decoded: object = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return None
    answer = as_mapping(decoded)
    if answer is None:
        return None
    values = answer.get("Answer")
    if not isinstance(values, list) or not values:
        return None
    if not all(isinstance(value, str) and value.strip() for value in values):
        return None
    if question.question_type in {
        QuizQuestionType.SINGLE,
        QuizQuestionType.JUDGEMENT,
        QuizQuestionType.SHORT_ANSWER,
    } and len(values) != 1:
        return None
    return [value.strip() for value in values]


OpenAICompatibleProvider = OpenAICompatibleAnswerProvider
