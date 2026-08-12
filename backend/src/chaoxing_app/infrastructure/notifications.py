from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol
from urllib.parse import SplitResult, urlsplit

import requests

type RequestTimeout = tuple[float, float]

_TELEGRAM_API_ROOT: Final = "https://api.telegram.org"
_TELEGRAM_TOKEN = re.compile(r"^[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


class NotificationChannel(StrEnum):
    SERVER_CHAN = "server_chan"
    QMSG = "qmsg"
    BARK = "bark"
    TELEGRAM = "telegram"


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    title: str = field(repr=False)
    body: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise NotificationConfigurationError("notification title must not be empty")
        if not isinstance(self.body, str) or not self.body.strip():
            raise NotificationConfigurationError("notification body must not be empty")
        if _CONTROL_CHARACTER.search(self.title.replace("\n", "")):
            raise NotificationConfigurationError("notification title contains control characters")
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "body", self.body.strip())

    @property
    def plain_text(self) -> str:
        return f"{self.title}\n{self.body}"


@dataclass(frozen=True, slots=True)
class NotificationResult:
    channel: NotificationChannel
    delivered: bool
    status_code: int
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.delivered


class NotificationSender(Protocol):
    def send(self, message: NotificationMessage) -> NotificationResult: ...


class NotificationError(Exception):
    """Base class for sanitized failures at a notification provider boundary."""


class NotificationConfigurationError(NotificationError):
    """Raised when notification configuration is incomplete or unsafe."""


class NotificationTransportError(NotificationError):
    def __init__(self, channel: NotificationChannel) -> None:
        self.channel = channel
        super().__init__(f"{channel.value} notification request failed")


class NotificationTimeoutError(NotificationTransportError):
    def __init__(self, channel: NotificationChannel) -> None:
        self.channel = channel
        NotificationError.__init__(self, f"{channel.value} notification request timed out")


class NotificationHTTPError(NotificationError):
    def __init__(self, channel: NotificationChannel, status_code: int) -> None:
        self.channel = channel
        self.status_code = status_code
        super().__init__(
            f"{channel.value} notification provider returned HTTP {status_code}"
        )


class NotificationResponseError(NotificationError):
    def __init__(self, channel: NotificationChannel) -> None:
        self.channel = channel
        super().__init__(f"{channel.value} notification provider returned an invalid response")


def _validate_timeout(timeout: RequestTimeout) -> RequestTimeout:
    if len(timeout) != 2 or any(
        isinstance(value, bool) or not isinstance(value, (float, int)) or value <= 0
        for value in timeout
    ):
        raise NotificationConfigurationError("notification request timeouts must be positive")
    return float(timeout[0]), float(timeout[1])


def _validate_tls(tls_verify: bool | str) -> bool | str:
    if tls_verify is False or (isinstance(tls_verify, str) and not tls_verify.strip()):
        raise NotificationConfigurationError("notification TLS verification cannot be disabled")
    if not isinstance(tls_verify, (bool, str)):
        raise NotificationConfigurationError("invalid notification TLS configuration")
    return tls_verify.strip() if isinstance(tls_verify, str) else tls_verify


def _split_webhook_url(webhook_url: str) -> SplitResult:
    if not isinstance(webhook_url, str) or not webhook_url.strip():
        raise NotificationConfigurationError("notification webhook URL must not be empty")
    try:
        parsed = urlsplit(webhook_url.strip())
        _ = parsed.port
    except ValueError:
        raise NotificationConfigurationError("notification webhook URL is invalid") from None
    return parsed


def validate_notification_webhook_url(webhook_url: str) -> str:
    parsed = _split_webhook_url(webhook_url)
    if parsed.scheme.casefold() != "https":
        raise NotificationConfigurationError("notification webhook must use HTTPS")
    if not parsed.hostname:
        raise NotificationConfigurationError("notification webhook URL requires a host")
    if parsed.username is not None or parsed.password is not None:
        raise NotificationConfigurationError("notification webhook URL cannot contain user info")
    if parsed.fragment:
        raise NotificationConfigurationError("notification webhook URL cannot contain a fragment")
    return parsed.geturl()


def validate_telegram_bot_token(bot_token: str) -> str:
    if not isinstance(bot_token, str) or not _TELEGRAM_TOKEN.fullmatch(bot_token.strip()):
        raise NotificationConfigurationError("Telegram bot token is invalid")
    return bot_token.strip()


def validate_telegram_chat_id(chat_id: str) -> str:
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise NotificationConfigurationError("Telegram chat id must not be empty")
    normalized = chat_id.strip()
    if _CONTROL_CHARACTER.search(normalized):
        raise NotificationConfigurationError("Telegram chat id is invalid")
    return normalized


def _json_object(response: requests.Response, channel: NotificationChannel) -> dict[str, object]:
    try:
        payload: object = response.json()
    except ValueError:
        raise NotificationResponseError(channel) from None
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise NotificationResponseError(channel)
    return payload


def _integer_status(value: object, channel: NotificationChannel) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationResponseError(channel)
    return value


class _NotificationHTTPClient:
    channel: NotificationChannel

    def __init__(
        self,
        *,
        session: requests.Session,
        timeout: RequestTimeout,
        tls_verify: bool | str,
    ) -> None:
        self._session = session
        self._timeout = _validate_timeout(timeout)
        self._tls_verify = _validate_tls(tls_verify)

    def _post(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        json_body: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        try:
            response = self._session.request(
                "POST",
                url,
                params=params,
                data=data,
                json=json_body,
                headers=headers,
                timeout=self._timeout,
                verify=self._tls_verify,
            )
        except requests.Timeout:
            raise NotificationTimeoutError(self.channel) from None
        except requests.RequestException:
            raise NotificationTransportError(self.channel) from None
        if not 200 <= response.status_code < 300:
            raise NotificationHTTPError(self.channel, response.status_code)
        return response

    def _result(self, response: requests.Response, *, delivered: bool) -> NotificationResult:
        return NotificationResult(
            channel=self.channel,
            delivered=delivered,
            status_code=response.status_code,
            reason="" if delivered else "provider_rejected",
        )


class _WebhookNotificationSender(_NotificationHTTPClient):
    def __init__(
        self,
        *,
        webhook_url: str,
        session: requests.Session,
        timeout: RequestTimeout,
        tls_verify: bool | str,
    ) -> None:
        super().__init__(session=session, timeout=timeout, tls_verify=tls_verify)
        self._webhook_url = validate_notification_webhook_url(webhook_url)


class ServerChanNotificationSender(_WebhookNotificationSender):
    channel = NotificationChannel.SERVER_CHAN

    def __init__(
        self,
        *,
        webhook_url: str,
        session: requests.Session,
        timeout: RequestTimeout = (5.0, 15.0),
        tls_verify: bool | str = True,
    ) -> None:
        super().__init__(
            webhook_url=webhook_url,
            session=session,
            timeout=timeout,
            tls_verify=tls_verify,
        )

    def send(self, message: NotificationMessage) -> NotificationResult:
        response = self._post(
            self._webhook_url,
            json_body={"text": message.title, "desp": message.body},
            headers={"Content-Type": "application/json;charset=utf-8"},
        )
        payload = _json_object(response, self.channel)
        if "code" in payload:
            delivered = _integer_status(payload["code"], self.channel) == 0
        elif "errno" in payload:
            delivered = _integer_status(payload["errno"], self.channel) == 0
        else:
            raise NotificationResponseError(self.channel)
        return self._result(response, delivered=delivered)


class QmsgNotificationSender(_WebhookNotificationSender):
    channel = NotificationChannel.QMSG

    def __init__(
        self,
        *,
        webhook_url: str,
        session: requests.Session,
        timeout: RequestTimeout = (5.0, 15.0),
        tls_verify: bool | str = True,
    ) -> None:
        super().__init__(
            webhook_url=webhook_url,
            session=session,
            timeout=timeout,
            tls_verify=tls_verify,
        )

    def send(self, message: NotificationMessage) -> NotificationResult:
        response = self._post(
            self._webhook_url,
            params={"msg": message.plain_text},
            headers={"Content-Type": "application/json;charset=utf-8"},
        )
        payload = _json_object(response, self.channel)
        success = payload.get("success")
        if not isinstance(success, bool):
            raise NotificationResponseError(self.channel)
        if "code" in payload and _integer_status(payload["code"], self.channel) != 0:
            success = False
        return self._result(response, delivered=success)


class BarkNotificationSender(_WebhookNotificationSender):
    channel = NotificationChannel.BARK

    def __init__(
        self,
        *,
        webhook_url: str,
        session: requests.Session,
        timeout: RequestTimeout = (5.0, 15.0),
        tls_verify: bool | str = True,
    ) -> None:
        super().__init__(
            webhook_url=webhook_url,
            session=session,
            timeout=timeout,
            tls_verify=tls_verify,
        )

    def send(self, message: NotificationMessage) -> NotificationResult:
        response = self._post(
            self._webhook_url,
            params={"title": message.title, "body": message.body},
        )
        payload = _json_object(response, self.channel)
        if "code" not in payload:
            raise NotificationResponseError(self.channel)
        delivered = _integer_status(payload["code"], self.channel) == 200
        return self._result(response, delivered=delivered)


class TelegramNotificationSender(_NotificationHTTPClient):
    channel = NotificationChannel.TELEGRAM

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        session: requests.Session,
        timeout: RequestTimeout = (5.0, 15.0),
        tls_verify: bool | str = True,
    ) -> None:
        super().__init__(session=session, timeout=timeout, tls_verify=tls_verify)
        self._bot_token = validate_telegram_bot_token(bot_token)
        self._chat_id = validate_telegram_chat_id(chat_id)

    def send(self, message: NotificationMessage) -> NotificationResult:
        response = self._post(
            f"{_TELEGRAM_API_ROOT}/bot{self._bot_token}/sendMessage",
            data={"chat_id": self._chat_id, "text": message.plain_text},
        )
        payload = _json_object(response, self.channel)
        ok = payload.get("ok")
        if not isinstance(ok, bool):
            raise NotificationResponseError(self.channel)
        return self._result(response, delivered=ok)

    @staticmethod
    def _validate_bot_token(bot_token: str) -> str:
        return validate_telegram_bot_token(bot_token)

    @staticmethod
    def _validate_chat_id(chat_id: str) -> str:
        return validate_telegram_chat_id(chat_id)


ServerChanSender = ServerChanNotificationSender
QmsgSender = QmsgNotificationSender
BarkSender = BarkNotificationSender
TelegramSender = TelegramNotificationSender
