from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.domain.settings import RunWindowSettings
from chaoxing_app.infrastructure.db.models import SystemSettings

SYSTEM_SETTINGS_ID = 1


@dataclass(frozen=True, slots=True)
class SystemSettingsUpdate:
    worker_enabled: bool | None = None
    run_window_enabled: bool | None = None
    run_window_start: str | None = None
    run_window_end: str | None = None
    timezone: str | None = None
    event_retention_days: int | None = None


def to_run_window_settings(model: SystemSettings) -> RunWindowSettings:
    return RunWindowSettings(
        worker_enabled=model.worker_enabled,
        run_window_enabled=model.run_window_enabled,
        run_window_start=model.run_window_start,
        run_window_end=model.run_window_end,
        timezone=model.timezone,
    )


class SystemSettingsRepository:
    """Owns the singleton settings row and validates every persisted update."""

    def get(self, session: Session) -> SystemSettings:
        model = session.get(SystemSettings, SYSTEM_SETTINGS_ID)
        if model is not None:
            return model

        # The supported database is SQLite.  OR IGNORE makes first access safe
        # even if two application instances are accidentally started together.
        session.execute(
            insert(SystemSettings)
            .values(
                id=SYSTEM_SETTINGS_ID,
                worker_enabled=True,
                run_window_enabled=False,
                run_window_start="00:00",
                run_window_end="00:00",
                timezone="Asia/Shanghai",
                event_retention_days=30,
            )
            .prefix_with("OR IGNORE")
        )
        session.flush()
        model = session.get(SystemSettings, SYSTEM_SETTINGS_ID)
        if model is None:  # pragma: no cover - defensive database invariant
            raise RuntimeError("system settings row could not be initialized")
        return model

    def update(self, session: Session, changes: SystemSettingsUpdate) -> SystemSettings:
        model = self.get(session)
        validated = RunWindowSettings(
            worker_enabled=(
                changes.worker_enabled
                if changes.worker_enabled is not None
                else model.worker_enabled
            ),
            run_window_enabled=(
                changes.run_window_enabled
                if changes.run_window_enabled is not None
                else model.run_window_enabled
            ),
            run_window_start=(
                changes.run_window_start
                if changes.run_window_start is not None
                else model.run_window_start
            ),
            run_window_end=(
                changes.run_window_end
                if changes.run_window_end is not None
                else model.run_window_end
            ),
            timezone=changes.timezone if changes.timezone is not None else model.timezone,
        )
        model.worker_enabled = validated.worker_enabled
        model.run_window_enabled = validated.run_window_enabled
        model.run_window_start = validated.run_window_start
        model.run_window_end = validated.run_window_end
        model.timezone = validated.timezone
        if changes.event_retention_days is not None:
            if not 1 <= changes.event_retention_days <= 3650:
                raise ValueError("event retention must be between 1 and 3650 days")
            model.event_retention_days = changes.event_retention_days
        session.flush()
        session.refresh(model)
        return model


class DatabaseRunWindowSettingsLoader:
    """Short-lived-session adapter for the supervisor's scheduler gate."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._repository = SystemSettingsRepository()

    def load_run_window_settings(self) -> RunWindowSettings:
        with self._session_factory() as session:
            model = self._repository.get(session)
            settings = to_run_window_settings(model)
            session.commit()
            return settings
