from __future__ import annotations

from collections import deque
from typing import Any, cast

import pytest
import requests

from chaoxing_app.platform.captcha import ChaoxingCaptchaSolver, is_captcha_challenge
from chaoxing_app.platform.errors import PlatformCaptchaError, PlatformConfigurationError


def response(
    content: bytes = b"",
    *,
    status_code: int = 200,
    content_type: str = "text/plain",
    url: str = "https://sanitized.example.test/result",
) -> requests.Response:
    result = requests.Response()
    result.status_code = status_code
    result.url = url
    result.headers["Content-Type"] = content_type
    result._content = content
    result.encoding = "utf-8"
    return result


class StubSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append((method, url, kwargs))
        return self.responses.popleft()


class StubRecognizer:
    def __init__(self, results: list[str]) -> None:
        self.results = deque(results)
        self.images: list[bytes] = []

    def recognize(self, image: bytes) -> str:
        self.images.append(image)
        return self.results.popleft()


def test_solver_uses_account_session_and_preserves_tls_verification() -> None:
    session = StubSession(
        [
            response(b"png", content_type="image/png; charset=binary"),
            response(status_code=302),
        ]
    )
    recognizer = StubRecognizer(["A12b"])
    solver = ChaoxingCaptchaSolver(
        session=cast(requests.Session, session),
        recognizer=recognizer,
        nonce=lambda: 42,
    )

    solver.solve()

    assert recognizer.images == [b"png"]
    assert session.calls[0][2]["params"] == {"t": 42}
    assert session.calls[0][2]["verify"] is True
    assert session.calls[1][2]["params"] == {"ucode": "A12b", "app": 0}
    assert session.calls[1][2]["allow_redirects"] is False
    assert session.calls[1][2]["verify"] is True


def test_solver_retries_invalid_recognition_without_submitting_it() -> None:
    session = StubSession(
        [
            response(b"first", content_type="image/png"),
            response(b"second", content_type="image/png"),
            response(status_code=302),
        ]
    )
    solver = ChaoxingCaptchaSolver(
        session=cast(requests.Session, session),
        recognizer=StubRecognizer(["bad token!", "1234"]),
        nonce=lambda: 1,
    )

    solver.solve()

    assert len(session.calls) == 3
    assert session.calls[-1][2]["params"]["ucode"] == "1234"


def test_solver_fails_after_a_bounded_number_of_attempts() -> None:
    session = StubSession(
        [response(b"png", content_type="image/png"), response(status_code=200)]
    )
    solver = ChaoxingCaptchaSolver(
        session=cast(requests.Session, session),
        recognizer=StubRecognizer(["1234"]),
        max_attempts=1,
    )

    with pytest.raises(PlatformCaptchaError, match="verification failed"):
        solver.solve()


def test_challenge_detection_is_specific_to_verification_html() -> None:
    challenge = response(
        b'<img src="/processVerifyPng.ac">',
        content_type="text/html; charset=utf-8",
    )
    ordinary_html = response(b"<html>maintenance</html>", content_type="text/html")

    assert is_captcha_challenge(challenge) is True
    assert is_captcha_challenge(ordinary_html) is False


def test_solver_rejects_insecure_or_unbounded_configuration() -> None:
    session = cast(requests.Session, StubSession([]))
    with pytest.raises(PlatformConfigurationError):
        ChaoxingCaptchaSolver(session=session, tls_verify=False)
    with pytest.raises(PlatformConfigurationError):
        ChaoxingCaptchaSolver(session=session, max_attempts=0)
