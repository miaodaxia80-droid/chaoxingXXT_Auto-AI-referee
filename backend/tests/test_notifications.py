from __future__ import annotations

import json
from collections import deque
from typing import Any, cast

import pytest
import requests

from chaoxing_app.infrastructure.notifications import (
    BarkNotificationSender,
    NotificationChannel,
    NotificationConfigurationError,
    NotificationHTTPError,
    NotificationMessage,
    NotificationResponseError,
    NotificationTimeoutError,
    NotificationTransportError,
    QmsgNotificationSender,
    ServerChanNotificationSender,
    TelegramNotificationSender,
)

WEBHOOK = "https://notify.example.test/private-webhook-token"
BOT_TOKEN = "123456:private-bot-token"
CHAT_ID = "-100123456789"


def json_response(
    payload: object,
    *,
    status_code: int = 200,
    url: str = "https://sanitized.example.test/result",
) -> requests.Response:
    result = requests.Response()
    result.status_code = status_code
    result.url = url
    result.encoding = "utf-8"
    result._content = json.dumps(payload).encode("utf-8")
    result.headers["Content-Type"] = "application/json"
    return result


def text_response(
    payload: str,
    *,
    status_code: int = 200,
    url: str = "https://sanitized.example.test/result",
) -> requests.Response:
    result = requests.Response()
    result.status_code = status_code
    result.url = url
    result.encoding = "utf-8"
    result._content = payload.encode("utf-8")
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


def message() -> NotificationMessage:
    return NotificationMessage(
        title="Study task finished",
        body="Account private-user completed Private Course",
    )


def assert_standard_transport(call: tuple[str, str, dict[str, Any]]) -> None:
    method, _url, kwargs = call
    assert method == "POST"
    assert kwargs["timeout"] == (2.0, 9.0)
    assert kwargs["verify"] is True


def test_server_chan_request_contract_and_strict_success_payload() -> None:
    session = StubSession([json_response({"code": 0, "message": "SUCCESS"})])
    sender = ServerChanNotificationSender(
        webhook_url=WEBHOOK,
        session=cast(requests.Session, session),
        timeout=(2.0, 9.0),
    )

    result = sender.send(message())

    assert result.channel is NotificationChannel.SERVER_CHAN
    assert result.delivered is True
    assert result.status_code == 200
    assert_standard_transport(session.calls[0])
    _, url, kwargs = session.calls[0]
    assert url == WEBHOOK
    assert kwargs["json"] == {
        "text": "Study task finished",
        "desp": "Account private-user completed Private Course",
    }
    assert kwargs["params"] is None
    assert kwargs["data"] is None
    assert kwargs["headers"] == {"Content-Type": "application/json;charset=utf-8"}


def test_qmsg_request_contract_and_success_requires_boolean_flag() -> None:
    session = StubSession([json_response({"success": True, "code": 0})])
    sender = QmsgNotificationSender(
        webhook_url=WEBHOOK,
        session=cast(requests.Session, session),
        timeout=(2.0, 9.0),
    )

    result = sender.send(message())

    assert result == result.__class__(NotificationChannel.QMSG, True, 200)
    assert_standard_transport(session.calls[0])
    _, url, kwargs = session.calls[0]
    assert url == WEBHOOK
    assert kwargs["params"] == {
        "msg": "Study task finished\nAccount private-user completed Private Course"
    }
    assert kwargs["json"] is None
    assert kwargs["data"] is None

    invalid = StubSession([json_response({"success": 1, "code": 0})])
    with pytest.raises(NotificationResponseError):
        QmsgNotificationSender(
            webhook_url=WEBHOOK,
            session=cast(requests.Session, invalid),
        ).send(message())


def test_bark_request_contract_and_code_200_success() -> None:
    session = StubSession([json_response({"code": 200, "message": "success"})])
    sender = BarkNotificationSender(
        webhook_url=WEBHOOK,
        session=cast(requests.Session, session),
        timeout=(2.0, 9.0),
    )

    result = sender.send(message())

    assert result.channel is NotificationChannel.BARK
    assert result.accepted is True
    assert_standard_transport(session.calls[0])
    _, url, kwargs = session.calls[0]
    assert url == WEBHOOK
    assert kwargs["params"] == {
        "title": "Study task finished",
        "body": "Account private-user completed Private Course",
    }
    assert kwargs["json"] is None
    assert kwargs["data"] is None


def test_telegram_constructs_only_the_official_https_api_request() -> None:
    session = StubSession([json_response({"ok": True, "result": {"message_id": 7}})])
    sender = TelegramNotificationSender(
        bot_token=BOT_TOKEN,
        chat_id=CHAT_ID,
        session=cast(requests.Session, session),
        timeout=(2.0, 9.0),
    )

    result = sender.send(message())

    assert result.channel is NotificationChannel.TELEGRAM
    assert result.delivered is True
    assert_standard_transport(session.calls[0])
    _, url, kwargs = session.calls[0]
    assert url == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    assert kwargs["data"] == {
        "chat_id": CHAT_ID,
        "text": "Study task finished\nAccount private-user completed Private Course",
    }
    assert kwargs["params"] is None
    assert kwargs["json"] is None


@pytest.mark.parametrize(
    ("sender", "payload"),
    [
        ("server_chan", {"code": 1, "message": "private provider detail"}),
        ("qmsg", {"success": False, "code": 1, "reason": "private provider detail"}),
        ("bark", {"code": 400, "message": "private provider detail"}),
        ("telegram", {"ok": False, "description": "private provider detail"}),
    ],
)
def test_provider_rejection_returns_only_sanitized_metadata(
    sender: str,
    payload: dict[str, object],
) -> None:
    session = StubSession([json_response(payload, url=WEBHOOK)])
    if sender == "server_chan":
        client = ServerChanNotificationSender(
            webhook_url=WEBHOOK,
            session=cast(requests.Session, session),
        )
    elif sender == "qmsg":
        client = QmsgNotificationSender(
            webhook_url=WEBHOOK,
            session=cast(requests.Session, session),
        )
    elif sender == "bark":
        client = BarkNotificationSender(
            webhook_url=WEBHOOK,
            session=cast(requests.Session, session),
        )
    else:
        client = TelegramNotificationSender(
            bot_token=BOT_TOKEN,
            chat_id=CHAT_ID,
            session=cast(requests.Session, session),
        )

    result = client.send(message())

    assert result.delivered is False
    assert result.reason == "provider_rejected"
    serialized = repr(result)
    assert "private provider detail" not in serialized
    assert "private-webhook-token" not in serialized
    assert BOT_TOKEN not in serialized
    assert CHAT_ID not in serialized
    assert "Private Course" not in serialized


def test_http_transport_timeout_and_json_errors_are_sanitized() -> None:
    cases: list[
        tuple[
            requests.Response | requests.RequestException,
            type[Exception],
        ]
    ] = [
        (
            text_response(
                "Private Course provider body",
                status_code=503,
                url=WEBHOOK,
            ),
            NotificationHTTPError,
        ),
        (
            requests.ConnectionError(
                f"failed URL {WEBHOOK}, body=Private Course, chat={CHAT_ID}"
            ),
            NotificationTransportError,
        ),
        (
            requests.Timeout(f"timed out URL {WEBHOOK}, body=Private Course"),
            NotificationTimeoutError,
        ),
        (
            text_response("Private Course is not JSON", url=WEBHOOK),
            NotificationResponseError,
        ),
    ]

    for outcome, error_type in cases:
        session = StubSession([outcome])
        sender = ServerChanNotificationSender(
            webhook_url=WEBHOOK,
            session=cast(requests.Session, session),
        )
        with pytest.raises(error_type) as raised:
            sender.send(message())
        serialized = repr(raised.value) + str(raised.value)
        assert "private-webhook-token" not in serialized
        assert "Private Course" not in serialized
        assert CHAT_ID not in serialized
        assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://notify.example.test/private-token",
        "ftp://notify.example.test/private-token",
        "notify.example.test/private-token",
        "https:///private-token",
        "https://user:private-token@notify.example.test/hook",
        "https://notify.example.test/hook#private-token",
        "https://notify.example.test:invalid/private-token",
    ],
)
@pytest.mark.parametrize(
    "sender_type",
    [ServerChanNotificationSender, QmsgNotificationSender, BarkNotificationSender],
)
def test_webhook_clients_reject_unsafe_or_invalid_urls_without_echoing_them(
    bad_url: str,
    sender_type: type[
        ServerChanNotificationSender | QmsgNotificationSender | BarkNotificationSender
    ],
) -> None:
    session = StubSession([])
    with pytest.raises(NotificationConfigurationError) as raised:
        sender_type(
            webhook_url=bad_url,
            session=cast(requests.Session, session),
        )
    assert "private-token" not in str(raised.value)


def test_tls_timeout_and_telegram_credentials_are_validated_without_secret_echo() -> None:
    session = StubSession([])
    with pytest.raises(NotificationConfigurationError, match="TLS verification"):
        ServerChanNotificationSender(
            webhook_url=WEBHOOK,
            session=cast(requests.Session, session),
            tls_verify=False,
        )
    with pytest.raises(NotificationConfigurationError, match="timeouts"):
        QmsgNotificationSender(
            webhook_url=WEBHOOK,
            session=cast(requests.Session, session),
            timeout=(0.0, 5.0),
        )
    with pytest.raises(NotificationConfigurationError) as token_error:
        TelegramNotificationSender(
            bot_token="private/token?secret",
            chat_id=CHAT_ID,
            session=cast(requests.Session, session),
        )
    assert "private/token" not in str(token_error.value)
    with pytest.raises(NotificationConfigurationError) as chat_error:
        TelegramNotificationSender(
            bot_token=BOT_TOKEN,
            chat_id="private-chat\nheader",
            session=cast(requests.Session, session),
        )
    assert "private-chat" not in str(chat_error.value)


def test_message_and_result_repr_never_include_notification_content() -> None:
    notification = message()
    assert "Private Course" not in repr(notification)
    assert "private-user" not in repr(notification)

    with pytest.raises(NotificationConfigurationError) as raised:
        NotificationMessage(title="", body="Private Course")
    assert "Private Course" not in str(raised.value)
