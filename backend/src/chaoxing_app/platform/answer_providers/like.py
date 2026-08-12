from __future__ import annotations

import re
from typing import ClassVar, Final

import requests

from chaoxing_app.platform.errors import PlatformConfigurationError
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
)

DEFAULT_LIKE_ENDPOINT: Final = "https://api.datam.site/search"
_CHOICE_SEPARATOR = re.compile(r"[\s,;|\uff0c\uff1b]+")
_QUESTION_PREFIX: Final[dict[QuizQuestionType, str]] = {
    QuizQuestionType.SINGLE: "\u3010\u5355\u9009\u9898\u3011",
    QuizQuestionType.MULTIPLE: "\u3010\u591a\u9009\u9898\u3011",
    QuizQuestionType.COMPLETION: "\u3010\u586b\u7a7a\u9898\u3011",
    QuizQuestionType.JUDGEMENT: "\u3010\u5224\u65ad\u9898\u3011",
}
_OTHER_PREFIX: Final = "\u3010\u5176\u4ed6\u7c7b\u578b\u9898\u76ee\u3011"


class LikeAnswerProvider(SafeAnswerProvider):
    capability: ClassVar = AnswerProviderCapability.LIKE
    provider_id: ClassVar = "like"

    def __init__(
        self,
        *,
        token: str,
        model: str | None = None,
        search: bool = False,
        endpoint: str = DEFAULT_LIKE_ENDPOINT,
        session: requests.Session | None = None,
        timeout: RequestTimeout = DEFAULT_TIMEOUT,
        tls_verify: TLSVerify = True,
        allow_unsafe_endpoint: bool = False,
    ) -> None:
        if not isinstance(search, bool):
            raise PlatformConfigurationError("answer provider search setting is invalid")
        self._token = require_nonempty(token, field="token")
        self._model = model.strip() if isinstance(model, str) else ""
        self._search = search
        self._http = JSONHTTPClient(
            endpoint=endpoint,
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
        del course_context
        prefix = _QUESTION_PREFIX.get(question.question_type, _OTHER_PREFIX)
        options = ", ".join(question.options)
        payload = self._http.request(
            "POST",
            json_body={
                "query": f"{prefix}{question.title}\n{options}",
                "token": self._token,
                "model": self._model,
                "search": self._search,
            },
        )
        return _parse_like_response(payload, question)


def _parse_like_response(payload: object, question: QuizQuestion) -> ProviderAnswer | None:
    envelope = as_mapping(payload)
    if envelope is None:
        return None
    data = as_mapping(envelope.get("data"))
    if data is None:
        return None
    answer_type = data.get("type")
    if type(answer_type) is not int:
        return None

    if answer_type == 0:
        answer = data.get("others")
        if not isinstance(answer, str):
            return None
        return answer_from_values([answer], source="like")
    if answer_type == 1:
        choices = _like_choices(data.get("choose"), question)
        return answer_from_values(choices, source="like") if choices is not None else None
    if answer_type == 2:
        fills = data.get("fills")
        if not isinstance(fills, list) or not all(isinstance(item, str) for item in fills):
            return None
        return answer_from_values(fills, source="like")
    if answer_type == 3:
        judgement = data.get("judge")
        if type(judgement) is not int or judgement not in {0, 1}:
            return None
        return answer_from_values(
            ["\u6b63\u786e" if judgement == 1 else "\u9519\u8bef"],
            source="like",
        )
    return None


def _like_choices(value: object, question: QuizQuestion) -> list[str] | None:
    raw_codes: list[str]
    if isinstance(value, str):
        compact = _CHOICE_SEPARATOR.sub("", value).upper()
        raw_codes = list(compact)
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        raw_codes = [item.strip().upper() for item in value]
    else:
        return None
    if not raw_codes or any(
        len(code) != 1 or not code.isascii() or not code.isalpha()
        for code in raw_codes
    ):
        return None
    if len(raw_codes) != len(set(raw_codes)):
        return None
    choices: list[str] = []
    for code in raw_codes:
        index = ord(code) - ord("A")
        if index < 0 or index >= len(question.options):
            return None
        choices.append(question.options[index])
    return choices


LikeProvider = LikeAnswerProvider
