from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_BYTES = 32
_NONCE_BYTES = 12
_FORMAT_VERSION = b"\x01"


class MasterKeyStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self) -> bytes:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return self._read()

        key = AESGCM.generate_key(bit_length=256)
        encoded = base64.urlsafe_b64encode(key)
        try:
            with os.fdopen(file_descriptor, "wb") as key_file:
                key_file.write(encoded)
                key_file.flush()
                os.fsync(key_file.fileno())
        except BaseException:
            self.path.unlink(missing_ok=True)
            raise
        return key

    def load(self) -> bytes:
        """Read an existing key without silently creating a replacement."""
        return self._read()

    def _read(self) -> bytes:
        encoded = self.path.read_bytes().strip()
        try:
            key = base64.urlsafe_b64decode(encoded)
        except (ValueError, TypeError) as exc:
            raise ValueError("master key file is not valid base64") from exc
        if len(key) != _KEY_BYTES:
            raise ValueError("master key must contain 32 bytes")
        return key


class SecretBox:
    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise ValueError("AES-GCM key must contain 32 bytes")
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: str, *, purpose: str) -> str:
        if not purpose:
            raise ValueError("purpose must not be empty")
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext.encode("utf-8"),
            purpose.encode("utf-8"),
        )
        envelope = _FORMAT_VERSION + nonce + ciphertext
        return base64.urlsafe_b64encode(envelope).decode("ascii")

    def decrypt(self, envelope: str, *, purpose: str) -> str:
        if not purpose:
            raise ValueError("purpose must not be empty")
        raw = base64.urlsafe_b64decode(envelope.encode("ascii"))
        minimum_length = len(_FORMAT_VERSION) + _NONCE_BYTES + 16
        if len(raw) < minimum_length or raw[:1] != _FORMAT_VERSION:
            raise ValueError("unsupported encrypted secret format")
        nonce = raw[1 : 1 + _NONCE_BYTES]
        ciphertext = raw[1 + _NONCE_BYTES :]
        plaintext = self._cipher.decrypt(
            nonce,
            ciphertext,
            purpose.encode("utf-8"),
        )
        return plaintext.decode("utf-8")


def identity_fingerprint(identity: str, *, key: bytes) -> str:
    normalized = identity.strip().casefold().encode("utf-8")
    if not normalized:
        raise ValueError("identity must not be empty")
    return hmac.new(key, normalized, hashlib.sha256).hexdigest()
