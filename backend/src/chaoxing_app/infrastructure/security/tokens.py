from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionToken:
    plaintext: str
    digest: str


def digest_session_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("ascii")).hexdigest()


def derive_csrf_token(session_token: str) -> str:
    digest = hmac.new(
        session_token.encode("ascii"),
        b"chaoxing-console-csrf-v1",
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def issue_session_token() -> SessionToken:
    plaintext = secrets.token_urlsafe(32)
    return SessionToken(
        plaintext=plaintext,
        digest=digest_session_token(plaintext),
    )
