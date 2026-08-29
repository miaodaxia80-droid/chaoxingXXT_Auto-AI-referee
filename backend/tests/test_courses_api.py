from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoxing_app.application.course_discovery import (
    AccountCredentialsMissingError,
    AccountDisabledError,
    AccountNotFoundError,
)
from chaoxing_app.infrastructure.db.models import Account
from chaoxing_app.main import create_app
from chaoxing_app.platform.errors import (
    PlatformAuthenticationError,
    PlatformConfigurationError,
    PlatformHTTPError,
    PlatformParseError,
    PlatformTimeoutError,
    PlatformTransportError,
)
from chaoxing_app.platform.models import Chapter, Course, CourseOutline
from chaoxing_app.settings import AppSettings


def make_client(temp_dir: str) -> tuple[FastAPI, TestClient]:
    data_dir = Path(temp_dir) / "data"
    database_path = data_dir / "app.db"
    settings = AppSettings(
        environment="test",
        data_dir=data_dir,
        database_url=f"sqlite:///{database_path.as_posix()}",
    )
    app = create_app(settings)
    return app, TestClient(app)


def bootstrap_and_login(client: TestClient) -> str:
    setup = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert setup.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    return login.json()["csrf_token"]


def seed_account(app: FastAPI, account_id: int) -> None:
    with app.state.session_factory.begin() as session:
        session.add(
            Account(
                id=account_id,
                username_hint=f"u{account_id}",
                username_fingerprint=f"fp-{account_id}",
            )
        )


class StubCourseService:
    def __init__(
        self,
        *,
        courses: tuple[Course, ...] = (),
        outline: CourseOutline | None = None,
        error: Exception | None = None,
    ) -> None:
        self.courses = courses
        self.outline = outline or CourseOutline(chapters=())
        self.error = error
        self.course_account_ids: list[int] = []
        self.outline_calls: list[tuple[int, Course]] = []

    def list_courses(self, account_id: int) -> tuple[Course, ...]:
        self.course_account_ids.append(account_id)
        if self.error is not None:
            raise self.error
        return self.courses

    def get_course_outline(self, account_id: int, course: Course) -> CourseOutline:
        self.outline_calls.append((account_id, course))
        if self.error is not None:
            raise self.error
        return self.outline


def test_course_discovery_requires_authentication_and_csrf() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            app.state.course_discovery_service = StubCourseService()
            discover_url = "/api/v1/accounts/7/courses/discover"
            chapters_url = "/api/v1/accounts/7/courses/course-1/chapters"
            chapter_payload = {"class_id": "class-1", "cpi": "cpi-1", "title": "Course"}

            assert client.post(discover_url).status_code == 401
            assert client.post(chapters_url, json=chapter_payload).status_code == 401

            csrf_token = bootstrap_and_login(client)
            assert client.post(discover_url).status_code == 403
            assert client.post(chapters_url, json=chapter_payload).status_code == 403
            assert (
                client.post(
                    discover_url,
                    headers={"X-CSRF-Token": f"{csrf_token}-invalid"},
                ).status_code
                == 403
            )

            seed_account(app, 7)
            assert (
                client.post(
                    discover_url,
                    headers={"X-CSRF-Token": csrf_token},
                ).status_code
                == 200
            )


def test_course_discovery_returns_public_course_fields() -> None:
    courses = (
        Course(
            course_id="course-100",
            clazz_id="class-100",
            cpi="cpi-100",
            title="Course One",
            teacher="Teacher One",
            description="First fixture course",
            raw_info="must-not-be-returned",
        ),
        Course(
            course_id="course-200",
            clazz_id="class-200",
            cpi="cpi-200",
            title="Course Two",
            teacher="Teacher Two",
            description="Second fixture course",
        ),
    )
    service = StubCourseService(courses=courses)

    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            app.state.course_discovery_service = service
            seed_account(app, 42)

            response = client.post(
                "/api/v1/accounts/42/courses/discover",
                headers={"X-CSRF-Token": csrf_token},
            )

    assert response.status_code == 200
    assert response.json() == [
        {
            "course_id": "course-100",
            "class_id": "class-100",
            "cpi": "cpi-100",
            "title": "Course One",
            "teacher": "Teacher One",
            "description": "First fixture course",
        },
        {
            "course_id": "course-200",
            "class_id": "class-200",
            "cpi": "cpi-200",
            "title": "Course Two",
            "teacher": "Teacher Two",
            "description": "Second fixture course",
        },
    ]
    assert "must-not-be-returned" not in response.text
    assert service.course_account_ids == [42]


def test_chapter_discovery_builds_course_and_returns_positions() -> None:
    outline = CourseOutline(
        chapters=(
            Chapter(
                chapter_id="chapter-1",
                title="Chapter One",
                job_count=2,
                is_completed=False,
                requires_unlock=False,
            ),
            Chapter(
                chapter_id="chapter-2",
                title="Chapter Two",
                job_count=0,
                is_completed=True,
                requires_unlock=True,
            ),
        )
    )
    service = StubCourseService(outline=outline)

    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            app.state.course_discovery_service = service
            seed_account(app, 24)

            response = client.post(
                "/api/v1/accounts/24/courses/course-100/chapters",
                headers={"X-CSRF-Token": csrf_token},
                json={
                    "class_id": " class-100 ",
                    "cpi": " cpi-100 ",
                    "title": " Fixture Course ",
                },
            )

    assert response.status_code == 200
    assert response.json() == {
        "has_locked_chapters": True,
        "chapters": [
            {
                "chapter_id": "chapter-1",
                "title": "Chapter One",
                "position": 0,
                "job_count": 2,
                "is_completed": False,
                "requires_unlock": False,
            },
            {
                "chapter_id": "chapter-2",
                "title": "Chapter Two",
                "position": 1,
                "job_count": 0,
                "is_completed": True,
                "requires_unlock": True,
            },
        ],
    }
    account_id, requested_course = service.outline_calls[0]
    assert account_id == 24
    assert requested_course == Course(
        course_id="course-100",
        clazz_id="class-100",
        cpi="cpi-100",
        title="Fixture Course",
    )


def test_course_api_maps_runtime_and_platform_errors() -> None:
    cases = [
        (AccountNotFoundError("private detail"), 404, "account not found"),
        (AccountDisabledError("account is disabled"), 409, "account is disabled"),
        (
            AccountCredentialsMissingError("account has no usable credentials"),
            409,
            "account has no usable credentials",
        ),
        (
            PlatformAuthenticationError("upstream private detail"),
            422,
            "Chaoxing authentication failed",
        ),
        (PlatformTimeoutError("course list"), 504, "Chaoxing request timed out"),
        (
            PlatformConfigurationError("stored cookie data is invalid"),
            422,
            "stored cookie data is invalid",
        ),
        (
            PlatformTransportError("course list"),
            502,
            "Chaoxing returned an unusable response",
        ),
        (
            PlatformHTTPError("course list", 503),
            502,
            "Chaoxing returned an unusable response",
        ),
        (
            PlatformParseError("course list", "private parser detail"),
            502,
            "Chaoxing returned an unusable response",
        ),
    ]

    with tempfile.TemporaryDirectory() as temp_dir:
        app, client = make_client(temp_dir)
        with client:
            csrf_token = bootstrap_and_login(client)
            seed_account(app, 5)
            for error, expected_status, expected_detail in cases:
                app.state.course_discovery_service = StubCourseService(error=error)

                response = client.post(
                    "/api/v1/accounts/5/courses/discover",
                    headers={"X-CSRF-Token": csrf_token},
                )

                assert response.status_code == expected_status
                assert response.json() == {"detail": expected_detail}
