from __future__ import annotations

from typing import ClassVar, Final

import requests

from chaoxing_app.platform.task_points.quiz import AnswerProviderCapability

from ._common import DEFAULT_TIMEOUT, RequestTimeout, TLSVerify
from .openai_compatible import OpenAICompatibleAnswerProvider

DEFAULT_SILICONFLOW_BASE_URL: Final = "https://api.siliconflow.cn/v1"
DEFAULT_SILICONFLOW_MODEL: Final = "deepseek-ai/DeepSeek-V3"


class SiliconFlowAnswerProvider(OpenAICompatibleAnswerProvider):
    capability: ClassVar = AnswerProviderCapability.SILICONFLOW_ENSEMBLE
    provider_id: ClassVar = "siliconflow"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_SILICONFLOW_MODEL,
        base_url: str = DEFAULT_SILICONFLOW_BASE_URL,
        session: requests.Session | None = None,
        timeout: RequestTimeout = DEFAULT_TIMEOUT,
        tls_verify: TLSVerify = True,
        allow_unsafe_endpoint: bool = False,
    ) -> None:
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=api_key,
            session=session,
            timeout=timeout,
            tls_verify=tls_verify,
            allow_unsafe_endpoint=allow_unsafe_endpoint,
        )


SiliconFlowProvider = SiliconFlowAnswerProvider
SiliconFlowEnsembleAnswerProvider = SiliconFlowAnswerProvider
SiliconFlowEnsembleProvider = SiliconFlowAnswerProvider
