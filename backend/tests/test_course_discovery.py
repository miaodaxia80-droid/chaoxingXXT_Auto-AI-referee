from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
import requests
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.application.course_discovery import (
    AccountCredentialsMissingError,
    AccountDisabledError,
    AccountNotFoundError,
    CourseDiscoveryService,
)
from chaoxing_app.infrastructure.db.accounts import AccountRepository, NewAccount
from chaoxing_app.infrastructure.db.engine import (
    create_database_engine,
    create_schema,
    make_session_factory,
)
from chaoxing_app.infrastructure.db.models import AccountSecret
from chaoxing_app.infrastructure.security.secrets import SecretBox
from chaoxing_app.platform.client import ChaoxingClient
from chaoxing_app.platform.cookies import load_cookie_data
from chaoxing_app.platform.errors import PlatformAuthenticationError
from chaoxing_app.platform.models import (
    Chapter,
    Course,
    CourseOutline,
    LoginCredentials,
    LoginResult,
)

COURSE = Course(
    course_id="course-100",
    clazz_id="class-200",
    cpi="cpi-300",
    title="Fixture Course",
)
OUTLINE = CourseOutline(
    chapters=(
        Chapter(
            chapter_id="chapter-1",
            title="Chapter One",
            job_count=2,
            is_completed=False,
            requires_unlock=False,
        ),
    )
)


@dataclass(frozen=True, slots=True)
class DatabaseHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    secret_box: SecretBox
    fingerprint_key: bytes


@pytest.fixture
def database(tmp_path: Path) -> Iterator[DatabaseHarness]:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    create_schema(engine)
    harness = DatabaseHarness(
        engine=engine,
        sessions=make_session_factory(engine),
        secret_box=SecretBox(b"s" * 32),
        fingerprint_key=b"f" * 32,
    )
    try:
        yield harness
    finally:
        engine.dispose()


def add_account(
    database: DatabaseHarness,
    *,
    username: str,
    password: str | None = None,
    cookies: str | None = None,
    enabled: bool = True,
) -> int:
    repository = AccountRepository(
        secret_box=database.secret_box,
        fingerprint_key=database.fingerprint_key,
    )
    with database.sessions.begin() as session:
        account = repository.create(
            session,
            NewAccount(
                username=username,
                password=password,
                cookies=cookies,
                user_agent="Fixture Browser",
            ),
        )
        account.enabled = enabled
        return account.id


class RecordingClient:
    def __init__(
        self,
        session: requests.Session,
        *,
        authentication_failures: int,
        on_list: Callable[[requests.Session], None] | None,
    ) -> None:
        self.session = session
        self.authentication_failures = authentication_failures
        self.on_list = on_list
        self.login_calls: list[LoginCredentials] = []
        self.cookies_before_login: list[dict[str, str]] = []
        self.cookies_seen_by_list: list[dict[str, str]] = []
        self.outline_courses: list[Course] = []

    def login(self, credentials: LoginCredentials) -> LoginResult:
        self.login_calls.append(credentials)
        self.cookies_before_login.append(self.session.cookies.get_dict())
        self.session.cookies.set(
            "cx_auth",
            "fresh-session",
            domain=".chaoxing.com",
            path="/",
            secure=True,
        )
        return LoginResult(message="authenticated")

    def list_courses(self) -> tuple[Course, ...]:
        self.cookies_seen_by_list.append(self.session.cookies.get_dict())
        if len(self.cookies_seen_by_list) <= self.authentication_failures:
            raise PlatformAuthenticationError("platform session is not authenticated")
        if self.on_list is not None:
            self.on_list(self.session)
        return (COURSE,)

    def get_course_outline(self, course: Course) -> CourseOutline:
        self.outline_courses.append(course)
        return OUTLINE


class RecordingFactory:
    def __init__(
        self,
        *,
        authentication_failures: int = 0,
        on_list: Callable[[requests.Session], None] | None = None,
    ) -> None:
        self.authentication_failures = authentication_failures
        self.on_list = on_list
        self.clients: list[RecordingClient] = []
        self.user_agents: list[str] = []

    def __call__(self, session: requests.Session, user_agent: str) -> ChaoxingClient:
        client = RecordingClient(
            session,
            authentication_failures=self.authentication_failures,
            on_list=self.on_list,
        )
        self.clients.append(client)
        self.user_agents.append(user_agent)
        return cast(ChaoxingClient, client)


def make_service(
    database: DatabaseHarness,
    factory: RecordingFactory,
) -> CourseDiscoveryService:
    return CourseDiscoveryService(
        session_factory=database.sessions,
        secret_box=database.secret_box,
        client_factory=factory,
    )


def read_cookie_secret(database: DatabaseHarness, account_id: int) -> tuple[str | None, int]:
    with database.sessions() as session:
        secret = session.get(AccountSecret, account_id)
        assert secret is not None
        return secret.cookies_encrypted, secret.secret_version


def decrypt_cookie_secret(
    database: DatabaseHarness,
    account_id: int,
    encrypted: str,
) -> requests.cookies.RequestsCookieJar:
    serialized = database.secret_box.decrypt(
        encrypted,
        purpose=f"account:{account_id}:cookies",
    )
    jar = requests.cookies.RequestsCookieJar()
    load_cookie_data(jar, serialized)
    return jar


def test_password_login_runs_before_first_course_request(database: DatabaseHarness) -> None:
    account_id = add_account(
        database,
        username="password-user",
        password="upstream-password",
    )
    factory = RecordingFactory()

    courses = make_service(database, factory).list_courses(account_id)

    assert courses == (COURSE,)
    client = factory.clients[0]
    assert factory.user_agents == ["Fixture Browser"]
    assert len(client.login_calls) == 1
    assert client.login_calls[0].username == "password-user"
    assert client.login_calls[0].password == "upstream-password"
    assert client.cookies_before_login == [{}]
    assert client.cookies_seen_by_list == [{"cx_auth": "fresh-session"}]


def test_valid_stored_cookies_skip_password_login(database: DatabaseHarness) -> None:
    account_id = add_account(
        database,
        username="cookie-user",
        password="unused-password",
        cookies="_uid=12345; token=valid-session",
    )
    factory = RecordingFactory()

    courses = make_service(database, factory).list_courses(account_id)

    assert courses == (COURSE,)
    client = factory.clients[0]
    assert client.login_calls == []
    assert client.cookies_seen_by_list == [
        {"_uid": "12345", "token": "valid-session"}
    ]


def test_expired_cookies_fall_back_to_password_login(database: DatabaseHarness) -> None:
    account_id = add_account(
        database,
        username="fallback-user",
        password="fallback-password",
        cookies="token=expired-session",
    )
    factory = RecordingFactory(authentication_failures=1)

    courses = make_service(database, factory).list_courses(account_id)

    assert courses == (COURSE,)
    client = factory.clients[0]
    assert len(client.login_calls) == 1
    assert client.login_calls[0].password == "fallback-password"
    assert client.cookies_before_login == [{}]
    assert client.cookies_seen_by_list == [
        {"token": "expired-session"},
        {"cx_auth": "fresh-session"},
    ]
    encrypted, version = read_cookie_secret(database, account_id)
    assert encrypted is not None
    assert version == 2
    assert decrypt_cookie_secret(database, account_id, encrypted).get_dict() == {
        "cx_auth": "fresh-session"
    }


def test_refreshed_cookies_are_encrypted_and_persisted(database: DatabaseHarness) -> None:
    account_id = add_account(
        database,
        username="refresh-user",
        password="unused-password",
        cookies="token=initial-session",
    )

    def rotate_cookie(session: requests.Session) -> None:
        session.cookies.clear()
        session.cookies.set(
            "token",
            "rotated-session",
            domain=".chaoxing.com",
            path="/",
            secure=True,
        )

    factory = RecordingFactory(on_list=rotate_cookie)

    make_service(database, factory).list_courses(account_id)

    encrypted, version = read_cookie_secret(database, account_id)
    assert encrypted is not None
    assert version == 2
    assert "rotated-session" not in encrypted
    jar = decrypt_cookie_secret(database, account_id, encrypted)
    cookie = next(iter(jar))
    assert cookie.name == "token"
    assert cookie.value == "rotated-session"
    assert cookie.domain == ".chaoxing.com"
    assert cookie.secure is True


def test_cookie_refresh_cas_does_not_overwrite_concurrent_secret_edit(
    database: DatabaseHarness,
) -> None:
    account_id = add_account(
        database,
        username="concurrent-user",
        password="account-password",
        cookies="token=runtime-session",
    )
    operator_cookie = "token=operator-session"

    def edit_secret_concurrently(_session: requests.Session) -> None:
        with database.sessions.begin() as db:
            secret = db.get(AccountSecret, account_id)
            assert secret is not None
            secret.cookies_encrypted = database.secret_box.encrypt(
                operator_cookie,
                purpose=f"account:{account_id}:cookies",
            )
            secret.secret_version += 1

    factory = RecordingFactory(on_list=edit_secret_concurrently)

    make_service(database, factory).list_courses(account_id)

    encrypted, version = read_cookie_secret(database, account_id)
    assert encrypted is not None
    assert version == 2
    assert (
        database.secret_box.decrypt(
            encrypted,
            purpose=f"account:{account_id}:cookies",
        )
        == operator_cookie
    )


def test_missing_disabled_and_credentialless_accounts_fail_before_client_creation(
    database: DatabaseHarness,
) -> None:
    disabled_id = add_account(
        database,
        username="disabled-user",
        password="password",
        enabled=False,
    )
    credentialless_id = add_account(database, username="credentialless-user")
    factory = RecordingFactory()
    service = make_service(database, factory)

    with pytest.raises(AccountNotFoundError, match="account not found"):
        service.list_courses(99_999)
    with pytest.raises(AccountDisabledError, match="account is disabled"):
        service.list_courses(disabled_id)
    with pytest.raises(AccountCredentialsMissingError, match="no usable credentials"):
        service.list_courses(credentialless_id)

    assert factory.clients == []


def test_course_outline_uses_the_requested_course(database: DatabaseHarness) -> None:
    account_id = add_account(
        database,
        username="outline-user",
        cookies="token=valid-session",
    )
    factory = RecordingFactory()

    outline = make_service(database, factory).get_course_outline(account_id, COURSE)

    assert outline == OUTLINE
    assert factory.clients[0].outline_courses == [COURSE]
