from __future__ import annotations

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
)

_OPTION_PREFIX = re.compile(r"^\s*[A-Za-z](?:[.\s:\u3001\uff0e\uff1a\uff09)]|$)+")
_QUESTION_TYPE: Final[dict[QuizQuestionType, int]] = {
    QuizQuestionType.SINGLE: 0,
    QuizQuestionType.MULTIPLE: 1,
    QuizQuestionType.COMPLETION: 2,
    QuizQuestionType.JUDGEMENT: 3,
}


class TikuAdapterAnswerProvider(SafeAnswerProvider):
    capability: ClassVar = AnswerProviderCapability.TIKU_ADAPTER
    provider_id: ClassVar = "tiku_adapter"

    def __init__(
        self,
        *,
        endpoint: str,
        session: requests.Session | None = None,
        timeout: RequestTimeout = DEFAULT_TIMEOUT,
        tls_verify: TLSVerify = True,
        allow_unsafe_endpoint: bool = False,
    ) -> None:
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
        payload = self._http.request(
            "POST",
            json_body={
                "question": question.title,
                "options": [_OPTION_PREFIX.sub("", option, count=1) for option in question.options],
                "type": _QUESTION_TYPE.get(question.question_type, 4),
            },
        )
        envelope = as_mapping(payload)
        if envelope is None:
            return None
        answer = as_mapping(envelope.get("answer"))
        if answer is None:
            return None
        best = answer.get("bestAnswer")
        if not isinstance(best, list) or not all(isinstance(item, str) for item in best):
            return None
        return answer_from_values(best, source=self.provider_id)


TikuAdapterProvider = TikuAdapterAnswerProvider
