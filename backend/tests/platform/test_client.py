from __future__ import annotations

import json
import unittest
from collections import deque
from pathlib import Path
from typing import Any, cast

import requests

from chaoxing_app.platform.client import ChaoxingClient
from chaoxing_app.platform.errors import (
    PlatformAuthenticationError,
    PlatformConfigurationError,
    PlatformHTTPError,
    PlatformTimeoutError,
)
from chaoxing_app.platform.models import LoginCredentials
from chaoxing_app.platform.parsers import parse_course_list

FIXTURES = Path(__file__).with_name("fixtures")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def response_from_fixture(name: str, *, status_code: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.url = "https://sanitized.example.test/result"
    response.encoding = "utf-8"
    response._content = fixture(name).encode("utf-8")
    return response


class StubSession:
    def __init__(
        self,
        responses: list[requests.Response] | None = None,
        error: requests.RequestException | None = None,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.responses = deque(responses or [])
        self.error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        return self.responses.popleft()


class ChaoxingClientTests(unittest.TestCase):
    def test_login_encrypts_credentials_and_keeps_tls_verification_enabled(self) -> None:
        session = StubSession([response_from_fixture("login_success.json")])
        client = ChaoxingClient(session=cast(requests.Session, session))

        result = client.login(LoginCredentials("13800138000", "p@ssw0rd"))

        self.assertEqual(result.message, "login accepted")
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", ChaoxingClient.LOGIN_URL))
        self.assertEqual(kwargs["verify"], True)
        self.assertEqual(kwargs["timeout"], (5.0, 15.0))
        self.assertEqual(kwargs["data"]["uname"], "yYcVatS+4J+JGnm88UM56A==")
        self.assertEqual(kwargs["data"]["password"], "L76rF/6Y7TNTb/zSeM6/nA==")
        self.assertNotIn("13800138000", json.dumps(kwargs["data"]))
        self.assertNotIn("p@ssw0rd", json.dumps(kwargs["data"]))

    def test_rejected_login_raises_typed_error_without_credentials(self) -> None:
        session = StubSession([response_from_fixture("login_failure.json")])
        client = ChaoxingClient(session=cast(requests.Session, session))

        with self.assertRaises(PlatformAuthenticationError) as raised:
            client.login(LoginCredentials("private-user", "private-password"))

        self.assertEqual(str(raised.exception), "invalid credentials")
        self.assertNotIn("private-user", str(raised.exception))
        self.assertNotIn("private-password", str(raised.exception))

    def test_lists_root_and_folder_courses_without_duplicates(self) -> None:
        session = StubSession(
            [
                response_from_fixture("course_list_root.html"),
                response_from_fixture("course_folders.html"),
                response_from_fixture("course_list_folder.html"),
            ]
        )
        client = ChaoxingClient(session=cast(requests.Session, session))

        courses = client.list_courses()

        self.assertEqual([course.course_id for course in courses], ["course-100", "course-200"])
        self.assertEqual(session.calls[0][2]["data"]["courseFolderId"], "0")
        self.assertEqual(session.calls[2][2]["data"]["courseFolderId"], "folder-7")

    def test_checks_authentication_with_one_root_course_request(self) -> None:
        session = StubSession([response_from_fixture("course_list_root.html")])
        client = ChaoxingClient(session=cast(requests.Session, session))

        client.check_authentication()

        self.assertEqual(len(session.calls), 1)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", ChaoxingClient.COURSE_LIST_URL))
        self.assertEqual(kwargs["data"]["courseFolderId"], "0")
        self.assertEqual(kwargs["headers"]["Referer"], ChaoxingClient.COURSE_REFERER)

    def test_authentication_check_rejects_a_redirected_login_page(self) -> None:
        response = response_from_fixture("course_list_root.html")
        response.url = "https://passport2.chaoxing.com/login"
        session = StubSession([response])
        client = ChaoxingClient(session=cast(requests.Session, session))

        with self.assertRaises(PlatformAuthenticationError):
            client.check_authentication()

    def test_gets_typed_course_outline(self) -> None:
        course = parse_course_list(fixture("course_list_root.html"))[0]
        session = StubSession([response_from_fixture("chapter_list.html")])
        client = ChaoxingClient(session=cast(requests.Session, session))

        outline = client.get_course_outline(course)

        self.assertEqual(
            [chapter.chapter_id for chapter in outline.chapters],
            ["10001", "10002", "10003"],
        )
        params = session.calls[0][2]["params"]
        self.assertEqual(params["courseid"], course.course_id)
        self.assertEqual(params["clazzid"], course.clazz_id)

    def test_timeout_and_http_failures_are_typed(self) -> None:
        timeout_session = StubSession(error=requests.Timeout())
        timeout_client = ChaoxingClient(session=cast(requests.Session, timeout_session))
        with self.assertRaises(PlatformTimeoutError):
            timeout_client.login(LoginCredentials("user", "password"))

        http_session = StubSession(
            [response_from_fixture("login_failure.json", status_code=503)]
        )
        http_client = ChaoxingClient(session=cast(requests.Session, http_session))
        with self.assertRaises(PlatformHTTPError) as raised:
            http_client.login(LoginCredentials("user", "password"))
        self.assertEqual(raised.exception.status_code, 503)

    def test_tls_verification_cannot_be_disabled(self) -> None:
        with self.assertRaises(PlatformConfigurationError):
            ChaoxingClient(tls_verify=False)


if __name__ == "__main__":
    unittest.main()
