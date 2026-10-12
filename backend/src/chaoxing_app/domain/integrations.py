from __future__ import annotations

from enum import StrEnum

ANSWER_TIMEOUT_DEFAULT_SECONDS = 60
ANSWER_TIMEOUT_MIN_SECONDS = 10
ANSWER_TIMEOUT_MAX_SECONDS = 300


class AnswerProviderKind(StrEnum):
    YANXI = "yanxi"
    LIKE = "like"
    TIKU_ADAPTER = "tiku_adapter"
    OPENAI_COMPATIBLE = "openai_compatible"
    SILICONFLOW = "siliconflow"


class AnswerSubmitMode(StrEnum):
    AUTO = "auto"
    SAVE_ONLY = "save_only"
    SUBMIT = "submit"


class NotificationChannelKind(StrEnum):
    SERVER_CHAN = "server_chan"
    QMSG = "qmsg"
    BARK = "bark"
    TELEGRAM = "telegram"
