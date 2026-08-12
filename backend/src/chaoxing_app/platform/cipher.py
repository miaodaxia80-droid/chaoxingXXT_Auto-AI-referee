from __future__ import annotations

import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGIN_KEY = b"u2oh6Vu^HWe4_AES"


def encrypt_login_value(plaintext: str) -> str:
    """Encode a login field using the AES-CBC scheme expected by Chaoxing."""
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_LOGIN_KEY), modes.CBC(_LOGIN_KEY)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")
