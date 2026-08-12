from __future__ import annotations

import secrets
from collections.abc import Callable
from importlib import import_module
from threading import Lock
from typing import Protocol, cast

import requests

from chaoxing_app.platform.errors import (
    PlatformCaptchaError,
    PlatformConfigurationError,
)

_CAPTCHA_IMAGE_URL = "https://mooc1.chaoxing.com/processVerifyPng.ac"
_CAPTCHA_SUBMIT_URL = "https://mooc1.chaoxing.com/html/processVerify.ac"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class CaptchaRecognizer(Protocol):
    def recognize(self, image: bytes) -> str: ...


class CaptchaSolver(Protocol):
    def solve(self) -> None: ...


class _DdddOcrEngine(Protocol):
    def classification(self, image: bytes) -> object: ...


class DdddOcrRecognizer:
    """Lazily initializes one process-local OCR model and serializes inference."""

    def __init__(self) -> None:
        self._engine: _DdddOcrEngine | None = None
        self._lock = Lock()

    def recognize(self, image: bytes) -> str:
        with self._lock:
            if self._engine is None:
                try:
                    module = import_module("ddddocr")
                    constructor = module.DdddOcr
                    self._engine = cast(_DdddOcrEngine, constructor(show_ad=False))
                except (ImportError, AttributeError, TypeError) as exc:
                    raise PlatformCaptchaError("captcha OCR is unavailable") from exc
            result = self._engine.classification(image)
        if not isinstance(result, str):
            raise PlatformCaptchaError("captcha OCR returned an invalid result")
        return result


_DEFAULT_RECOGNIZER = DdddOcrRecognizer()


class ChaoxingCaptchaSolver:
    def __init__(
        self,
        *,
        session: requests.Session,
        recognizer: CaptchaRecognizer = _DEFAULT_RECOGNIZER,
        timeout: tuple[float, float] = (5.0, 15.0),
        tls_verify: bool | str = True,
        max_attempts: int = 3,
        nonce: Callable[[], int] = lambda: secrets.randbelow(2_147_483_648),
    ) -> None:
        if len(timeout) != 2 or any(value <= 0 for value in timeout):
            raise PlatformConfigurationError("request timeouts must be positive")
        if tls_verify is False or (isinstance(tls_verify, str) and not tls_verify.strip()):
            raise PlatformConfigurationError("TLS verification cannot be disabled")
        if not 1 <= max_attempts <= 10:
            raise PlatformConfigurationError("captcha attempts must be between one and ten")
        self._session = session
        self._recognizer = recognizer
        self._timeout = timeout
        self._tls_verify = tls_verify
        self._max_attempts = max_attempts
        self._nonce = nonce

    def solve(self) -> None:
        for _attempt in range(self._max_attempts):
            image = self._fetch_image()
            if image is None:
                continue
            token = self._recognizer.recognize(image).strip()
            if not _valid_token(token):
                continue
            if self._submit(token):
                return
        raise PlatformCaptchaError("platform captcha verification failed")

    def _fetch_image(self) -> bytes | None:
        try:
            response = self._session.request(
                "GET",
                _CAPTCHA_IMAGE_URL,
                params={"t": self._nonce()},
                timeout=self._timeout,
                verify=self._tls_verify,
            )
        except requests.RequestException:
            return None
        content_type = response.headers.get("Content-Type", "").partition(";")[0].casefold()
        if response.status_code != 200 or content_type != "image/png" or not response.content:
            return None
        return response.content

    def _submit(self, token: str) -> bool:
        params: dict[str, str | int] = {"ucode": token, "app": 0}
        try:
            response = self._session.request(
                "GET",
                _CAPTCHA_SUBMIT_URL,
                params=params,
                allow_redirects=False,
                timeout=self._timeout,
                verify=self._tls_verify,
            )
        except requests.RequestException:
            return False
        return response.status_code in _REDIRECT_STATUSES


def is_captcha_challenge(response: requests.Response) -> bool:
    url = response.url.casefold()
    if "processverify" in url:
        return True
    content_type = response.headers.get("Content-Type", "").casefold()
    if "html" not in content_type:
        return False
    body = response.text.casefold()
    return "processverifypng.ac" in body or "html/processverify.ac" in body


def _valid_token(token: str) -> bool:
    return 1 <= len(token) <= 12 and token.isascii() and token.isalnum()
