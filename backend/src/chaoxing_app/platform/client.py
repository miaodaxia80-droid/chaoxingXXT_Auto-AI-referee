from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import requests

from chaoxing_app.platform.cipher import encrypt_login_value
from chaoxing_app.platform.errors import (
    PlatformAuthenticationError,
    PlatformConfigurationError,
    PlatformHTTPError,
    PlatformParseError,
    PlatformTimeoutError,
    PlatformTransportError,
)
from chaoxing_app.platform.models import Course, CourseOutline, LoginCredentials, LoginResult
from chaoxing_app.platform.parsers import (
    parse_chapter_list,
    parse_course_folders,
    parse_course_list,
)

RequestValues = Mapping[str, str | int | bool]
RequestTimeout = tuple[float, float]

DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ChaoxingClient:
    LOGIN_URL: Final = "https://passport2.chaoxing.com/fanyalogin"
    COURSE_LIST_URL: Final = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
    COURSE_INTERACTION_URL: Final = (
        "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
    )
    CHAPTER_LIST_URL: Final = (
        "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse"
    )
    COURSE_REFERER: Final = (
        "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?"
        "moocDomain=https://mooc1-1.chaoxing.com/mooc-ans"
    )

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: RequestTimeout = (5.0, 15.0),
        tls_verify: bool | str = True,
    ) -> None:
        if not user_agent.strip():
            raise PlatformConfigurationError("user agent must not be empty")
        if len(timeout) != 2 or any(value <= 0 for value in timeout):
            raise PlatformConfigurationError("request timeouts must be positive")
        if tls_verify is False or (isinstance(tls_verify, str) and not tls_verify.strip()):
            raise PlatformConfigurationError("TLS verification cannot be disabled")

        self._session = session or requests.Session()
        self._timeout = timeout
        self._tls_verify = tls_verify
        self._session.headers.update(
            {
                "User-Agent": user_agent.strip(),
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
        )

    def login(self, credentials: LoginCredentials) -> LoginResult:
        response = self._request(
            "POST",
            self.LOGIN_URL,
            operation="login",
            data={
                "fid": "-1",
                "uname": encrypt_login_value(credentials.username),
                "password": encrypt_login_value(credentials.password),
                "refer": "https%3A%2F%2Fi.chaoxing.com",
                "t": True,
                "forbidotherlogin": 0,
                "validate": "",
                "doubleFactorLogin": 0,
                "independentId": 0,
            },
        )
        payload = self._json_object(response, "login response")
        message = self._message(payload)
        if payload.get("status") is not True:
            raise PlatformAuthenticationError(message or "platform rejected the credentials")
        return LoginResult(message=message or "authenticated")

    def list_courses(self) -> tuple[Course, ...]:
        courses = list(self._courses_in_folder(folder_id="0", operation="root course list"))
        interaction = self._request(
            "GET",
            self.COURSE_INTERACTION_URL,
            operation="course folders",
        )
        self._reject_login_page(interaction)
        for folder in parse_course_folders(interaction.text):
            courses.extend(
                self._courses_in_folder(
                    folder_id=folder.folder_id,
                    operation="folder course list",
                )
            )

        unique_courses: dict[tuple[str, str, str], Course] = {}
        for course in courses:
            unique_courses.setdefault(course.identity, course)
        return tuple(unique_courses.values())

    def check_authentication(self) -> None:
        """Verify the current session with one lightweight authenticated request."""

        response = self._request(
            "POST",
            self.COURSE_LIST_URL,
            operation="authentication check",
            data={
                "courseType": 1,
                "courseFolderId": "0",
                "query": "",
                "superstarClass": 0,
            },
            headers={"Referer": self.COURSE_REFERER},
        )
        self._reject_login_page(response)

    def get_course_outline(self, course: Course) -> CourseOutline:
        response = self._request(
            "GET",
            self.CHAPTER_LIST_URL,
            operation="chapter list",
            params={
                "courseid": course.course_id,
                "clazzid": course.clazz_id,
                "cpi": course.cpi,
                "ut": "s",
            },
        )
        self._reject_login_page(response)
        return parse_chapter_list(response.text)

    def _courses_in_folder(self, *, folder_id: str, operation: str) -> tuple[Course, ...]:
        response = self._request(
            "POST",
            self.COURSE_LIST_URL,
            operation=operation,
            data={
                "courseType": 1,
                "courseFolderId": folder_id,
                "query": "",
                "superstarClass": 0,
            },
            headers={"Referer": self.COURSE_REFERER},
        )
        self._reject_login_page(response)
        return parse_course_list(response.text)

    def _request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        params: RequestValues | None = None,
        data: RequestValues | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        try:
            response = self._session.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=self._timeout,
                verify=self._tls_verify,
            )
        except requests.Timeout as exc:
            raise PlatformTimeoutError(operation) from exc
        except requests.RequestException as exc:
            raise PlatformTransportError(operation) from exc
        if response.status_code != 200:
            raise PlatformHTTPError(operation, response.status_code)
        return response

    @staticmethod
    def _json_object(response: requests.Response, resource: str) -> dict[str, object]:
        try:
            payload: object = response.json()
        except ValueError as exc:
            raise PlatformParseError(resource, "response is not valid JSON") from exc
        if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
            raise PlatformParseError(resource, "expected a JSON object")
        return payload

    @staticmethod
    def _message(payload: Mapping[str, object]) -> str:
        for key in ("msg2", "msg", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _reject_login_page(response: requests.Response) -> None:
        if "passport2.chaoxing.com" in response.url or "用户登录" in response.text:
            raise PlatformAuthenticationError("platform session is not authenticated")
