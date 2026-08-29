from __future__ import annotations

from dataclasses import dataclass

import httpx


class WeChatClientError(RuntimeError):
    """Raised when code2session cannot produce a valid openid."""


class WeChatConfigurationError(WeChatClientError):
    """Raised when the appid/secret is missing or rejected by WeChat."""


class WeChatInvalidCodeError(WeChatClientError):
    """Raised when the login code is expired, reused, or malformed."""


class WeChatRateLimitedError(WeChatClientError):
    """Raised when WeChat throttles code2session calls."""


@dataclass(frozen=True, slots=True)
class WeChatSessionInfo:
    openid: str
    session_key: str


class WeChatClient:
    """Minimal synchronous wrapper around WeChat's jscode2session."""

    CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

    def __init__(
        self,
        appid: str,
        secret: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._appid = appid
        self._secret = secret
        self._timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._appid and self._secret)

    def exchange_code(self, code: str) -> WeChatSessionInfo:
        if not self.configured:
            raise WeChatConfigurationError("WeChat appid or secret is not configured")
        try:
            response = httpx.get(
                self.CODE2SESSION_URL,
                params={
                    "appid": self._appid,
                    "secret": self._secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise WeChatClientError("WeChat code2session request failed") from exc
        payload = self._parse_payload(response)
        errcode_raw = payload.get("errcode")
        if errcode_raw is not None:
            errcode = int(errcode_raw) if isinstance(errcode_raw, (int, str)) else -1
            raise self._map_error(errcode, str(payload.get("errmsg", "")))
        openid = payload.get("openid")
        session_key = payload.get("session_key")
        if not isinstance(openid, str) or not openid or not isinstance(session_key, str):
            raise WeChatClientError("WeChat code2session response missing openid/session_key")
        return WeChatSessionInfo(openid=openid, session_key=session_key)

    @staticmethod
    def _parse_payload(response: httpx.Response) -> dict[str, object]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeChatClientError("WeChat code2session returned non-JSON payload") from exc
        if not isinstance(payload, dict):
            raise WeChatClientError("WeChat code2session returned unexpected payload")
        return payload

    @staticmethod
    def _map_error(errcode: int, errmsg: str) -> WeChatClientError:
        if errcode == 40029 or errcode == 40163:
            return WeChatInvalidCodeError(f"WeChat login code is invalid: {errmsg}")
        if errcode in {40013, 40125, 41002}:
            return WeChatConfigurationError(f"WeChat appid/secret rejected: {errmsg}")
        if errcode in {45011, 40226, -1}:
            return WeChatRateLimitedError(f"WeChat code2session throttled: {errmsg}")
        return WeChatClientError(f"WeChat code2session failed ({errcode}): {errmsg}")
