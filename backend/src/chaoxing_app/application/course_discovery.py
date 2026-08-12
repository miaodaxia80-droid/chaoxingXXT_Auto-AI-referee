from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.application.account_runtime import (
    AccountCredentialsMissingError,
    AccountDisabledError,
    AccountNotFoundError,
    AccountRuntimeError,
    AccountRuntimeFactory,
    ClientFactory,
    ResultT,
)
from chaoxing_app.infrastructure.security.secrets import SecretBox
from chaoxing_app.platform.client import ChaoxingClient
from chaoxing_app.platform.models import Course, CourseOutline

__all__ = [
    "AccountCredentialsMissingError",
    "AccountDisabledError",
    "AccountNotFoundError",
    "AccountRuntimeError",
    "CourseDiscoveryService",
]


class CourseDiscoveryService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        secret_box: SecretBox,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._runtime_factory = (
            AccountRuntimeFactory(
                session_factory=session_factory,
                secret_box=secret_box,
                client_factory=client_factory,
            )
            if client_factory is not None
            else AccountRuntimeFactory(
                session_factory=session_factory,
                secret_box=secret_box,
            )
        )

    def list_courses(self, account_id: int) -> tuple[Course, ...]:
        return self._execute(account_id, lambda client: client.list_courses())

    def get_course_outline(self, account_id: int, course: Course) -> CourseOutline:
        return self._execute(account_id, lambda client: client.get_course_outline(course))

    def _execute(
        self,
        account_id: int,
        operation: Callable[[ChaoxingClient], ResultT],
    ) -> ResultT:
        with self._runtime_factory.open(account_id, require_identity=False) as runtime:
            return runtime.execute(operation)
