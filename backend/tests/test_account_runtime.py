from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
import requests
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.application.account_runtime import (
    AccountIdentityMissingError,
    AccountRuntimeFactory,
    AccountRuntimeStateError,
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
from chaoxing_app.platform.models import LoginCredentials, LoginResult


class CountingSecretBox(SecretBox):
    def __init__(self, key: bytes) -> None:
        super().__init__(key)
        self.decrypt_calls: list[str] = []

    def decrypt(self, envelope: str, *, purpose: str) -> str:
        self.decrypt_calls.append(purpose)
        return super().decrypt(envelope, purpose=purpose)


@dataclass(frozen=True, slots=True)
class DatabaseHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    secret_box: CountingSecretBox
    fingerprint_key: bytes


@pytest.fixture
def database(tmp_path: Path) -> Iterator[DatabaseHarness]:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}")
    create_schema(engine)
    harness = DatabaseHarness(
        engine=engine,
        sessions=make_session_factory(engine),
        secret_box=CountingSecretBox(b"s" * 32),
        fingerprint_key=b"f" * 32,
    )
    try:
        yield harness
    finally:
        engine.dispose()


def add_account(
    database: DatabaseHarness,
    *,
    username: str = "private-user",
    password: str | None = "private-password",
    cookies: str | None = None,
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
                user_agent="Runtime Browser",
            ),
        )
        return account.id


class TrackingSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.was_closed = False

    def close(self) -> None:
        self.was_closed = True
        super().close()


class TrackingSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[TrackingSession] = []

    def __call__(self) -> requests.Session:
        session = TrackingSession()
        self.sessions.append(session)
        return session


class RecordingClient:
    def __init__(self, session: requests.Session) -> None:
        self.session = session
        self.authentication_checks: list[dict[str, str]] = []
        self.login_calls: list[LoginCredentials] = []
        self.cookies_before_login: list[dict[str, str]] = []

    def check_authentication(self) -> None:
        cookies = self.session.cookies.get_dict()
        self.authentication_checks.append(cookies)
        if cookies.get("token") == "expired":
            raise PlatformAuthenticationError("platform session is not authenticated")

    def login(self, credentials: LoginCredentials) -> LoginResult:
        self.login_calls.append(credentials)
        self.cookies_before_login.append(self.session.cookies.get_dict())
        self.session.cookies.set("_uid", "runtime-user")
        self.session.cookies.set("fid", "runtime-fid")
        self.session.cookies.set("token", "fresh")
        return LoginResult(message="authenticated")


class RecordingClientFactory:
    def __init__(self) -> None:
        self.clients: list[RecordingClient] = []
        self.user_agents: list[str] = []

    def __call__(self, session: requests.Session, user_agent: str) -> ChaoxingClient:
        client = RecordingClient(session)
        self.clients.append(client)
        self.user_agents.append(user_agent)
        return cast(ChaoxingClient, client)


def make_runtime_factory(
    database: DatabaseHarness,
    client_factory: RecordingClientFactory,
    http_sessions: TrackingSessionFactory,
) -> AccountRuntimeFactory:
    return AccountRuntimeFactory(
        session_factory=database.sessions,
        secret_box=database.secret_box,
        client_factory=client_factory,
        http_session_factory=http_sessions,
    )


def read_cookie_secret(database: DatabaseHarness, account_id: int) -> tuple[str | None, int]:
    with database.sessions() as session:
        secret = session.get(AccountSecret, account_id)
        assert secret is not None
        return secret.cookies_encrypted, secret.secret_version


def decrypt_cookies(
    database: DatabaseHarness,
    account_id: int,
    encrypted: str,
) -> dict[str, str]:
    serialized = database.secret_box.decrypt(
        encrypted,
        purpose=f"account:{account_id}:cookies",
    )
    jar = requests.cookies.RequestsCookieJar()
    load_cookie_data(jar, serialized)
    return jar.get_dict()


def test_open_is_lazy_and_each_context_owns_an_isolated_http_session(
    database: DatabaseHarness,
) -> None:
    account_id = add_account(
        database,
        cookies="_uid=user-1; fid=fid-1; token=valid",
    )
    clients = RecordingClientFactory()
    http_sessions = TrackingSessionFactory()
    factory = make_runtime_factory(database, clients, http_sessions)

    pending_runtime = factory.open(account_id)

    assert database.secret_box.decrypt_calls == []
    assert http_sessions.sessions == []

    with pending_runtime as first:
        first_session = first.session
        assert first.client is cast(ChaoxingClient, clients.clients[0])
        assert first.user_id == "user-1"
        assert first.fid == "fid-1"
        assert clients.user_agents == ["Runtime Browser"]
        assert clients.clients[0].authentication_checks == [
            {"_uid": "user-1", "fid": "fid-1", "token": "valid"}
        ]

    with factory.open(account_id) as second:
        assert second.session is not first_session

    assert len(database.secret_box.decrypt_calls) == 6
    assert len(http_sessions.sessions) == 2
    assert all(session.was_closed for session in http_sessions.sessions)
    assert pending_runtime.cookies_persisted is None
    with pytest.raises(AccountRuntimeStateError, match="not open"):
        _ = pending_runtime.session


def test_expired_cookie_is_probed_then_replaced_with_password_login(
    database: DatabaseHarness,
) -> None:
    account_id = add_account(
        database,
        cookies="_uid=stale-user; fid=stale-fid; token=expired",
    )
    clients = RecordingClientFactory()
    http_sessions = TrackingSessionFactory()
    pending_runtime = make_runtime_factory(database, clients, http_sessions).open(account_id)

    with pending_runtime as runtime:
        assert runtime.user_id == "runtime-user"
        assert runtime.fid == "runtime-fid"

    client = clients.clients[0]
    assert [cookies["token"] for cookies in client.authentication_checks] == [
        "expired",
        "fresh",
    ]
    assert client.cookies_before_login == [{}]
    assert len(client.login_calls) == 1
    assert client.login_calls[0] == LoginCredentials(
        username="private-user",
        password="private-password",
    )
    encrypted, version = read_cookie_secret(database, account_id)
    assert encrypted is not None
    assert "runtime-user" not in encrypted
    assert version == 2
    assert decrypt_cookies(database, account_id, encrypted) == {
        "_uid": "runtime-user",
        "fid": "runtime-fid",
        "token": "fresh",
    }
    assert pending_runtime.cookies_persisted is True


def test_execute_retries_one_operation_after_session_expires(
    database: DatabaseHarness,
) -> None:
    account_id = add_account(
        database,
        cookies="_uid=user-1; fid=fid-1; token=valid",
    )
    clients = RecordingClientFactory()
    http_sessions = TrackingSessionFactory()
    attempts = 0

    def flaky_operation(_client: ChaoxingClient) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PlatformAuthenticationError("platform session is not authenticated")
        return "completed"

    with make_runtime_factory(database, clients, http_sessions).open(account_id) as runtime:
        assert runtime.execute(flaky_operation) == "completed"
        assert runtime.user_id == "runtime-user"

    assert attempts == 2
    assert clients.clients[0].cookies_before_login == [{}]


@pytest.mark.parametrize(
    ("cookies", "missing_name"),
    [
        ("fid=fid-1; token=valid", "_uid"),
        ("_uid=user-1; token=valid", "fid"),
    ],
)
def test_missing_identity_cookie_has_explicit_sanitized_error_and_closes_session(
    database: DatabaseHarness,
    cookies: str,
    missing_name: str,
) -> None:
    account_id = add_account(
        database,
        username="secret-username",
        password=None,
        cookies=cookies,
    )
    clients = RecordingClientFactory()
    http_sessions = TrackingSessionFactory()

    with (
        pytest.raises(AccountIdentityMissingError) as raised,
        make_runtime_factory(database, clients, http_sessions).open(account_id),
    ):
        pytest.fail("runtime must reject incomplete identity cookies")

    message = str(raised.value)
    assert missing_name in message
    assert str(account_id) in message
    assert "secret-username" not in message
    assert "valid" not in message
    assert http_sessions.sessions[0].was_closed is True


def test_cookie_close_uses_cas_and_does_not_overwrite_concurrent_operator_edit(
    database: DatabaseHarness,
) -> None:
    account_id = add_account(
        database,
        cookies="_uid=user-1; fid=fid-1; token=valid",
    )
    clients = RecordingClientFactory()
    http_sessions = TrackingSessionFactory()
    pending_runtime = make_runtime_factory(database, clients, http_sessions).open(account_id)
    operator_cookies = "_uid=operator-user; fid=operator-fid; token=operator-session"

    with pending_runtime as runtime:
        runtime.session.cookies.set("token", "runtime-rotated")
        with database.sessions.begin() as session:
            secret = session.get(AccountSecret, account_id)
            assert secret is not None
            secret.cookies_encrypted = database.secret_box.encrypt(
                operator_cookies,
                purpose=f"account:{account_id}:cookies",
            )
            secret.secret_version += 1

    encrypted, version = read_cookie_secret(database, account_id)
    assert encrypted is not None
    assert version == 2
    assert (
        database.secret_box.decrypt(
            encrypted,
            purpose=f"account:{account_id}:cookies",
        )
        == operator_cookies
    )
    assert pending_runtime.cookies_persisted is False


def test_cookie_only_account_propagates_failed_probe_without_password_fallback(
    database: DatabaseHarness,
) -> None:
    account_id = add_account(
        database,
        username="cookie-only-user",
        password=None,
        cookies="_uid=user-1; fid=fid-1; token=expired",
    )
    clients = RecordingClientFactory()
    http_sessions = TrackingSessionFactory()

    with (
        pytest.raises(PlatformAuthenticationError, match="not authenticated"),
        make_runtime_factory(database, clients, http_sessions).open(account_id),
    ):
        pytest.fail("runtime must reject expired cookie-only authentication")

    assert clients.clients[0].login_calls == []
    assert http_sessions.sessions[0].was_closed is True
