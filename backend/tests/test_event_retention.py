from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.events import maintain_event_retention
from chaoxing_app.infrastructure.db.models import Event


def test_archived_events_are_kept_for_one_full_retention_period(tmp_path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'events.db').as_posix()}")
    create_schema(engine)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    retention = timedelta(days=10)

    with Session(engine) as session:
        session.add_all(
            [
                Event(
                    kind="recent-active",
                    occurred_at=now - retention,
                ),
                Event(
                    kind="newly-archived",
                    occurred_at=now - retention - timedelta(seconds=1),
                ),
                Event(
                    kind="archive-boundary",
                    occurred_at=now - retention * 2,
                    archived_at=now - retention,
                ),
                Event(
                    kind="expired-archive",
                    occurred_at=now - retention * 2,
                    archived_at=now - retention - timedelta(seconds=1),
                ),
            ]
        )
        session.commit()

        archived, purged = maintain_event_retention(
            session,
            retention_days=10,
            now=now,
        )
        session.commit()

        assert (archived, purged) == (1, 1)
        events = {event.kind: event for event in session.scalars(select(Event).order_by(Event.id))}
        assert set(events) == {
            "recent-active",
            "newly-archived",
            "archive-boundary",
        }
        assert events["recent-active"].archived_at is None
        assert events["newly-archived"].archived_at == now.replace(tzinfo=None)
        assert events["archive-boundary"].archived_at is not None

    engine.dispose()
