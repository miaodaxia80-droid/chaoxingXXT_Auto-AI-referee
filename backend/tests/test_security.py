import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from chaoxing_app.infrastructure.security.passwords import PasswordService
from chaoxing_app.infrastructure.security.secrets import MasterKeyStore, SecretBox
from chaoxing_app.infrastructure.security.tokens import (
    derive_csrf_token,
    digest_session_token,
    issue_session_token,
)


class MasterKeyStoreTests(unittest.TestCase):
    def test_key_is_created_once_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MasterKeyStore(Path(temp_dir) / "master.key")
            first = store.load_or_create()
            second = store.load_or_create()

        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)

    def test_invalid_key_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "master.key"
            path.write_text("not-a-key", encoding="ascii")
            with self.assertRaises(ValueError):
                MasterKeyStore(path).load_or_create()

    def test_load_requires_an_existing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "master.key"
            with self.assertRaises(FileNotFoundError):
                MasterKeyStore(path).load()
            self.assertFalse(path.exists())


class SecretBoxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.box = SecretBox(AESGCM.generate_key(bit_length=256))

    def test_round_trip(self) -> None:
        encrypted = self.box.encrypt("secret-value", purpose="account:1:password")
        self.assertNotIn("secret-value", encrypted)
        self.assertEqual(
            self.box.decrypt(encrypted, purpose="account:1:password"),
            "secret-value",
        )

    def test_ciphertext_is_bound_to_its_purpose(self) -> None:
        encrypted = self.box.encrypt("secret-value", purpose="account:1:password")
        with self.assertRaises(InvalidTag):
            self.box.decrypt(encrypted, purpose="account:1:cookie")

    def test_wrong_key_cannot_decrypt(self) -> None:
        encrypted = self.box.encrypt("secret-value", purpose="account:1:password")
        other_box = SecretBox(AESGCM.generate_key(bit_length=256))
        with self.assertRaises(InvalidTag):
            other_box.decrypt(encrypted, purpose="account:1:password")


class PasswordServiceTests(unittest.TestCase):
    def test_hash_and_verify(self) -> None:
        service = PasswordService()
        password_hash = service.hash("correct horse battery staple")
        self.assertTrue(service.verify(password_hash, "correct horse battery staple"))
        self.assertFalse(service.verify(password_hash, "wrong"))
        self.assertNotIn("correct horse battery staple", password_hash)

    def test_empty_password_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PasswordService().hash("")


class SessionTokenTests(unittest.TestCase):
    def test_only_digest_needs_to_be_persisted(self) -> None:
        token = issue_session_token()
        self.assertNotEqual(token.plaintext, token.digest)
        self.assertEqual(token.digest, digest_session_token(token.plaintext))
        self.assertEqual(len(token.digest), 64)

    def test_csrf_token_is_stable_and_bound_to_session(self) -> None:
        first = issue_session_token()
        second = issue_session_token()
        first_csrf = derive_csrf_token(first.plaintext)
        self.assertEqual(first_csrf, derive_csrf_token(first.plaintext))
        self.assertNotEqual(first_csrf, derive_csrf_token(second.plaintext))
        self.assertNotEqual(first_csrf, first.plaintext)


if __name__ == "__main__":
    unittest.main()
