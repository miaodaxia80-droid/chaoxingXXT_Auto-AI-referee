from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chaoxing_app.domain.answer_profiles import normalize_answer_profile_override
from chaoxing_app.infrastructure.db.models import Account, AccountSecret, StudyTask
from chaoxing_app.infrastructure.security.secrets import SecretBox, identity_fingerprint


class DuplicateAccountError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NewAccount:
    username: str
    password: str | None = None
    cookies: str | None = None
    remark: str = ""
    user_agent: str = ""
    speed: float = 1.0
    chapter_concurrency: int = 1
    unopened_policy: str = "retry"
    answer_profile_override: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AccountUpdate:
    username: str | None = None
    password: str | None = None
    cookies: str | None = None
    remark: str | None = None
    user_agent: str | None = None
    speed: float | None = None
    chapter_concurrency: int | None = None
    unopened_policy: str | None = None
    enabled: bool | None = None
    clear_password: bool = False
    clear_cookies: bool = False
    answer_profile_override: dict[str, object] | None = None
    clear_answer_profile_override: bool = False


def mask_username(username: str) -> str:
    value = username.strip()
    if len(value) <= 2:
        return value[:1] + "*" * max(len(value) - 1, 0)
    if len(value) <= 6:
        return value[0] + "*" * (len(value) - 2) + value[-1]
    return value[:3] + "*" * (len(value) - 7) + value[-4:]


class AccountRepository:
    def __init__(self, *, secret_box: SecretBox, fingerprint_key: bytes) -> None:
        self._secret_box = secret_box
        self._fingerprint_key = fingerprint_key

    def create(self, session: Session, new_account: NewAccount) -> Account:
        username = new_account.username.strip()
        if not username:
            raise ValueError("username must not be empty")
        account = Account(
            remark=new_account.remark.strip(),
            username_hint=mask_username(username),
            username_fingerprint=identity_fingerprint(username, key=self._fingerprint_key),
            user_agent=new_account.user_agent.strip(),
            speed=new_account.speed,
            chapter_concurrency=new_account.chapter_concurrency,
            unopened_policy=new_account.unopened_policy,
            answer_profile_override=normalize_answer_profile_override(
                new_account.answer_profile_override
            ),
        )
        session.add(account)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DuplicateAccountError("account already exists") from exc

        account.secret = AccountSecret(
            username_encrypted=self._secret_box.encrypt(
                username, purpose=f"account:{account.id}:username"
            ),
            password_encrypted=(
                self._secret_box.encrypt(
                    new_account.password, purpose=f"account:{account.id}:password"
                )
                if new_account.password
                else None
            ),
            cookies_encrypted=(
                self._secret_box.encrypt(
                    new_account.cookies, purpose=f"account:{account.id}:cookies"
                )
                if new_account.cookies
                else None
            ),
        )
        session.flush()
        return account

    def list(self, session: Session) -> list[Account]:
        return list(session.scalars(select(Account).order_by(Account.id)))

    def get(self, session: Session, account_id: int) -> Account | None:
        return session.get(Account, account_id)

    def update(self, session: Session, account_id: int, changes: AccountUpdate) -> Account | None:
        account = self.get(session, account_id)
        if account is None:
            return None
        account_secret = account.secret

        if changes.clear_password and changes.password:
            raise ValueError("password cannot be set and cleared together")
        if changes.clear_cookies and changes.cookies:
            raise ValueError("cookies cannot be set and cleared together")

        secret_changed = False
        if changes.username is not None:
            username = changes.username.strip()
            if not username:
                raise ValueError("username must not be empty")
            account.username_hint = mask_username(username)
            account.username_fingerprint = identity_fingerprint(username, key=self._fingerprint_key)
            account_secret.username_encrypted = self._secret_box.encrypt(
                username, purpose=f"account:{account.id}:username"
            )
            secret_changed = True

        if changes.remark is not None:
            account.remark = changes.remark.strip()
        if changes.user_agent is not None:
            account.user_agent = changes.user_agent.strip()
        if changes.speed is not None:
            account.speed = changes.speed
        if changes.chapter_concurrency is not None:
            account.chapter_concurrency = changes.chapter_concurrency
        if changes.unopened_policy is not None:
            account.unopened_policy = changes.unopened_policy
        if changes.enabled is not None:
            account.enabled = changes.enabled
        if changes.clear_answer_profile_override:
            account.answer_profile_override = None
        elif changes.answer_profile_override is not None:
            account.answer_profile_override = normalize_answer_profile_override(
                changes.answer_profile_override
            )

        if changes.clear_password:
            account_secret.password_encrypted = None
            secret_changed = True
        elif changes.password:
            account_secret.password_encrypted = self._secret_box.encrypt(
                changes.password,
                purpose=f"account:{account.id}:password",
            )
            secret_changed = True

        if changes.clear_cookies:
            account_secret.cookies_encrypted = None
            secret_changed = True
        elif changes.cookies:
            account_secret.cookies_encrypted = self._secret_box.encrypt(
                changes.cookies,
                purpose=f"account:{account.id}:cookies",
            )
            secret_changed = True

        if secret_changed:
            account_secret.secret_version += 1

        try:
            session.flush()
        except IntegrityError as exc:
            raise DuplicateAccountError("account already exists") from exc
        return account

    def delete(self, session: Session, account_id: int) -> bool:
        existing_id = session.scalar(select(Account.id).where(Account.id == account_id))
        if existing_id is None:
            return False

        # StudyTask intentionally uses RESTRICT so account history cannot disappear by
        # accident. Account deletion is the explicit operation that removes that history.
        session.execute(delete(StudyTask).where(StudyTask.account_id == account_id))
        session.execute(delete(Account).where(Account.id == account_id))
        session.flush()
        return True

    def reveal_username(self, account: Account) -> str:
        return self._secret_box.decrypt(
            account.secret.username_encrypted,
            purpose=f"account:{account.id}:username",
        )
