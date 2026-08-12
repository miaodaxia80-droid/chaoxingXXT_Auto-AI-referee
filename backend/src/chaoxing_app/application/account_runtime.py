from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Self, TypeVar

import requests
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.infrastructure.db.models import Account, AccountSecret
from chaoxing_app.infrastructure.security.secrets import SecretBox
from chaoxing_app.platform.client import DEFAULT_USER_AGENT, ChaoxingClient
from chaoxing_app.platform.cookies import dump_cookie_data, load_cookie_data
from chaoxing_app.platform.errors import PlatformAuthenticationError
from chaoxing_app.platform.models import LoginCredentials


class AccountRuntimeError(ValueError):
    pass


class AccountNotFoundError(AccountRuntimeError):
    pass


class AccountDisabledError(AccountRuntimeError):
    pass


class AccountCredentialsMissingError(AccountRuntimeError):
    pass


class AccountIdentityMissingError(AccountRuntimeError):
    pass


class AccountIdentityAmbiguousError(AccountRuntimeError):
    pass


class AccountRuntimeStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _RuntimeAccount:
    account_id: int
    username: str
    password: str | None
    cookies: str | None
    user_agent: str
    secret_version: int


ClientFactory = Callable[[requests.Session, str], ChaoxingClient]
HTTPSessionFactory = Callable[[], requests.Session]
ResultT = TypeVar("ResultT")


def _default_client_factory(session: requests.Session, user_agent: str) -> ChaoxingClient:
    return ChaoxingClient(session=session, user_agent=user_agent)


class AccountRuntimeFactory:
    """Creates isolated, short-lived authenticated account runtimes."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        secret_box: SecretBox,
        client_factory: ClientFactory = _default_client_factory,
        http_session_factory: HTTPSessionFactory = requests.Session,
    ) -> None:
        self._session_factory = session_factory
        self._secret_box = secret_box
        self._client_factory = client_factory
        self._http_session_factory = http_session_factory

    def open(
        self,
        account_id: int,
        *,
        require_identity: bool = True,
    ) -> AccountRuntime:
        return AccountRuntime(
            factory=self,
            account_id=account_id,
            require_identity=require_identity,
        )

    def _load_account(self, account_id: int) -> _RuntimeAccount:
        with self._session_factory() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise AccountNotFoundError("account not found")
            if not account.enabled:
                raise AccountDisabledError("account is disabled")
            secret = account.secret
            username = self._secret_box.decrypt(
                secret.username_encrypted,
                purpose=f"account:{account.id}:username",
            )
            password = (
                self._secret_box.decrypt(
                    secret.password_encrypted,
                    purpose=f"account:{account.id}:password",
                )
                if secret.password_encrypted
                else None
            )
            cookies = (
                self._secret_box.decrypt(
                    secret.cookies_encrypted,
                    purpose=f"account:{account.id}:cookies",
                )
                if secret.cookies_encrypted
                else None
            )
            if not password and not cookies:
                raise AccountCredentialsMissingError("account has no usable credentials")
            return _RuntimeAccount(
                account_id=account.id,
                username=username,
                password=password,
                cookies=cookies,
                user_agent=account.user_agent or DEFAULT_USER_AGENT,
                secret_version=secret.secret_version,
            )

    def _persist_cookies(
        self,
        account: _RuntimeAccount,
        cookie_data: str | None,
    ) -> bool:
        encrypted = (
            self._secret_box.encrypt(
                cookie_data,
                purpose=f"account:{account.account_id}:cookies",
            )
            if cookie_data
            else None
        )
        with self._session_factory.begin() as session:
            updated_account_id: int | None = session.scalar(
                update(AccountSecret)
                .where(
                    AccountSecret.account_id == account.account_id,
                    AccountSecret.secret_version == account.secret_version,
                )
                .values(
                    cookies_encrypted=encrypted,
                    secret_version=AccountSecret.secret_version + 1,
                )
                .returning(AccountSecret.account_id)
            )
            return updated_account_id is not None


class AccountRuntime(AbstractContextManager["AccountRuntime"]):
    """Owns one account's decrypted credentials and HTTP session for a run."""

    def __init__(
        self,
        *,
        factory: AccountRuntimeFactory,
        account_id: int,
        require_identity: bool,
    ) -> None:
        self._factory = factory
        self._account_id = account_id
        self._require_identity = require_identity
        self._account: _RuntimeAccount | None = None
        self._session: requests.Session | None = None
        self._client: ChaoxingClient | None = None
        self._initial_cookies: str | None = None
        self._closed = False
        self._cookies_persisted: bool | None = None

    def __enter__(self) -> Self:
        if self._account is not None or self._session is not None:
            raise AccountRuntimeStateError("account runtime is already open")
        if self._closed:
            raise AccountRuntimeStateError("account runtime cannot be reopened")

        account = self._factory._load_account(self._account_id)
        http_session = self._factory._http_session_factory()
        try:
            if account.cookies:
                load_cookie_data(http_session.cookies, account.cookies)
            client = self._factory._client_factory(http_session, account.user_agent)
            self._account = account
            self._session = http_session
            self._client = client
            self._initial_cookies = dump_cookie_data(http_session.cookies)

            if not account.cookies:
                self._login()
            if self._require_identity:
                self.execute(lambda active_client: active_client.check_authentication())
                self._read_identity_cookie("_uid")
                self._read_identity_cookie("fid")
            return self
        except BaseException:
            http_session.close()
            self._account = None
            self._session = None
            self._client = None
            self._closed = True
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def client(self) -> ChaoxingClient:
        if self._client is None:
            raise AccountRuntimeStateError("account runtime is not open")
        return self._client

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            raise AccountRuntimeStateError("account runtime is not open")
        return self._session

    @property
    def user_id(self) -> str:
        return self._read_identity_cookie("_uid")

    @property
    def fid(self) -> str:
        return self._read_identity_cookie("fid")

    @property
    def cookies_persisted(self) -> bool | None:
        """Whether changed cookies won their CAS update; None means no update was needed."""

        return self._cookies_persisted

    def execute(
        self,
        operation: Callable[[ChaoxingClient], ResultT],
    ) -> ResultT:
        try:
            return operation(self.client)
        except PlatformAuthenticationError:
            account = self._require_account()
            if not account.password:
                raise
            self.session.cookies.clear()
            self._login()
            if self._require_identity:
                self._read_identity_cookie("_uid")
                self._read_identity_cookie("fid")
            return operation(self.client)

    def close(self) -> None:
        if self._closed:
            return
        account = self._require_account()
        http_session = self.session
        try:
            refreshed_cookies = dump_cookie_data(http_session.cookies)
            if refreshed_cookies != self._initial_cookies:
                self._cookies_persisted = self._factory._persist_cookies(
                    account,
                    refreshed_cookies,
                )
        finally:
            http_session.close()
            self._account = None
            self._session = None
            self._client = None
            self._closed = True

    def _login(self) -> None:
        account = self._require_account()
        if not account.password:
            raise PlatformAuthenticationError("platform session is not authenticated")
        self.client.login(
            LoginCredentials(username=account.username, password=account.password)
        )

    def _require_account(self) -> _RuntimeAccount:
        if self._account is None:
            raise AccountRuntimeStateError("account runtime is not open")
        return self._account

    def _read_identity_cookie(self, name: str) -> str:
        values: set[str] = set()
        for cookie in self.session.cookies:
            if cookie.name != name or cookie.value is None:
                continue
            value = cookie.value.strip()
            if value:
                values.add(value)
        if not values:
            raise AccountIdentityMissingError(
                f"account {self._account_id} authenticated session is missing {name} cookie"
            )
        if len(values) != 1:
            raise AccountIdentityAmbiguousError(
                f"account {self._account_id} authenticated session has conflicting {name} cookies"
            )
        return values.pop()
