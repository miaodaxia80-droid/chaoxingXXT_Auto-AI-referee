from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.infrastructure.db.base import Base

CURRENT_SCHEMA_REVISION = "20260812_0003"


class DatabaseSchemaError(RuntimeError):
    pass


def create_database_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA journal_mode=WAL")
            finally:
                cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    # Importing registers all mapped tables on Base.metadata.
    from chaoxing_app.infrastructure.db import models  # noqa: F401

    Base.metadata.create_all(engine)


def require_current_schema(engine: Engine) -> None:
    """Fail closed when a non-test database has not reached the Alembic head."""

    try:
        if "alembic_version" not in inspect(engine).get_table_names():
            raise DatabaseSchemaError(
                "database is not initialized by Alembic; run `python -m alembic upgrade head`"
            )
        with engine.connect() as connection:
            revisions = tuple(
                str(value)
                for value in connection.scalars(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                )
            )
    except DatabaseSchemaError:
        raise
    except SQLAlchemyError as exc:
        raise DatabaseSchemaError("database schema revision could not be inspected") from exc

    if revisions != (CURRENT_SCHEMA_REVISION,):
        current = ", ".join(revisions) if revisions else "none"
        raise DatabaseSchemaError(
            f"database schema revision is {current}; expected {CURRENT_SCHEMA_REVISION}. "
            "Run `python -m alembic upgrade head` before starting the service"
        )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
