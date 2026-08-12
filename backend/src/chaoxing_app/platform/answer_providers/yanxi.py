from __future__ import annotations

from collections.abc import Sequence
from threading import Lock
from typing import ClassVar, Final

import requests

from chaoxing_app.platform.errors import PlatformConfigurationError
from chaoxing_app.platform.task_points.quiz import (
    AnswerProviderCapability,
    ProviderAnswer,
    QuizQuestion,
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

DEFAULT_YANXI_ENDPOINT: Final = "https://tk.enncy.cn/query"
_TOKEN_EXHAUSTED_MARKER: Final = "\u6b21\u6570\u4e0d\u8db3"


class YanxiAnswerProvider(SafeAnswerProvider):
    capability: ClassVar = AnswerProviderCapability.YANXI
    provider_id: ClassVar = "yanxi"

    def __init__(
        self,
        *,
        tokens: str | Sequence[str],
        endpoint: str = DEFAULT_YANXI_ENDPOINT,
        session: requests.Session | None = None,
        timeout: RequestTimeout = DEFAULT_TIMEOUT,
        tls_verify: TLSVerify = True,
        allow_unsafe_endpoint: bool = False,
    ) -> None:
        raw_tokens: Sequence[object] = tokens.split(",") if isinstance(tokens, str) else tokens
        if any(not isinstance(token, str) for token in raw_tokens):
            raise PlatformConfigurationError("answer provider credentials are invalid")
        normalized = tuple(
            token.strip() for token in raw_tokens if isinstance(token, str) and token.strip()
        )
        if not normalized:
            raise PlatformConfigurationError("answer provider credentials are required")
        self._tokens = normalized
        self._token_index = 0
        self._token_lock = Lock()
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
        with self._token_lock:
            while self._token_index < len(self._tokens):
                token = self._tokens[self._token_index]
                payload = self._http.request(
                    "GET",
                    params={"question": question.title, "token": token},
                )
                answer, exhausted = _parse_yanxi_response(payload)
                if answer is not None:
                    return answer_from_values([answer], source=self.provider_id)
                if not exhausted:
                    return None
                self._token_index += 1
        return None


def _parse_yanxi_response(payload: object) -> tuple[str | None, bool]:
    data = as_mapping(payload)
    if data is None:
        return None, False
    code = data.get("code")
    body = as_mapping(data.get("data"))
    if body is None:
        return None, False
    raw_answer = body.get("answer")
    if code is True or (type(code) is int and code == 1):
        if isinstance(raw_answer, str) and raw_answer.strip():
            return raw_answer.strip(), False
        return None, False
    if code is False or (type(code) is int and code == 0):
        exhausted = (
            isinstance(raw_answer, str) and _TOKEN_EXHAUSTED_MARKER in raw_answer
        )
        return None, exhausted
    return None, False


YanxiProvider = YanxiAnswerProvider
