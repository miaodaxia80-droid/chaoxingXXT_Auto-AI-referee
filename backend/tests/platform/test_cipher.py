import unittest

from chaoxing_app.platform.cipher import encrypt_login_value
from chaoxing_app.platform.models import LoginCredentials


class LoginCipherTests(unittest.TestCase):
    def test_matches_legacy_username_vector(self) -> None:
        self.assertEqual(
            encrypt_login_value("13800138000"),
            "yYcVatS+4J+JGnm88UM56A==",
        )

    def test_matches_legacy_password_vector(self) -> None:
        self.assertEqual(
            encrypt_login_value("p@ssw0rd"),
            "L76rF/6Y7TNTb/zSeM6/nA==",
        )

    def test_credentials_do_not_expose_secrets_in_repr(self) -> None:
        credentials = LoginCredentials(username="private-user", password="private-password")
        rendered = repr(credentials)
        self.assertNotIn("private-user", rendered)
        self.assertNotIn("private-password", rendered)


if __name__ == "__main__":
    unittest.main()
