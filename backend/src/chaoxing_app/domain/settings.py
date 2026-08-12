"""Pure scheduling policies used by the worker and the settings API.

The persisted settings object deliberately contains strings for clock times so
that it stays portable across SQLite, JSON, and the browser.  This module is
the only place that interprets those values as a local-time run window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from re import fullmatch
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_clock_time(value: str) -> time:
    """Parse the API's ``HH:MM`` clock representation.

    Seconds are intentionally not accepted: the UI and scheduler tick at a
    minute-level boundary, and rejecting them prevents ambiguous persistence.
    """

    if not isinstance(value, str):
        raise ValueError("run window time must be a string")
    if fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is None:
        raise ValueError("run window time must use HH:MM")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("run window time must use HH:MM") from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValueError("run window time must use HH:MM")
    return parsed


def normalize_clock_time(value: str) -> str:
    parsed = parse_clock_time(value)
    return parsed.strftime("%H:%M")


def validate_timezone(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timezone must not be blank")
    normalized = value.strip()
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {normalized}") from exc
    return normalized


@dataclass(frozen=True, slots=True)
class RunWindowSettings:
    """Worker scheduling settings, independent of persistence or HTTP."""

    run_window_enabled: bool = False
    run_window_start: str = "00:00"
    run_window_end: str = "00:00"
    timezone: str = "Asia/Shanghai"
    worker_enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_window_start", normalize_clock_time(self.run_window_start))
        object.__setattr__(self, "run_window_end", normalize_clock_time(self.run_window_end))
        object.__setattr__(self, "timezone", validate_timezone(self.timezone))

    def is_run_window_open(self, now: datetime | None = None) -> bool:
        return is_run_window_open(now or datetime.now().astimezone(), self)


def is_run_window_open(
    now: datetime,
    settings: RunWindowSettings,
) -> bool:
    """Return whether a worker may claim work at ``now``.

    Equal start and end values represent an all-day window.  Otherwise the
    interval is half-open (start inclusive, end exclusive), including the
    common cross-midnight form such as ``23:00`` to ``07:00``.  ``now`` is
    converted into the configured IANA timezone before comparison.
    """

    if not settings.worker_enabled:
        return False
    if not settings.run_window_enabled:
        return True

    zone = ZoneInfo(settings.timezone)
    local_now = (
        now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)
    ).timetz().replace(tzinfo=None)
    start = parse_clock_time(settings.run_window_start)
    end = parse_clock_time(settings.run_window_end)
    if start == end:
        return True
    if start < end:
        return start <= local_now < end
    return local_now >= start or local_now < end


class RunWindowSettingsLoader(Protocol):
    def load_run_window_settings(self) -> RunWindowSettings: ...


class SchedulerGate:
    """Small callable adapter useful for injecting a settings-backed gate."""

    def __init__(self, loader: RunWindowSettingsLoader) -> None:
        self._loader = loader

    def __call__(self, now: datetime | None = None) -> bool:
        # The loader protocol is intentionally duck-typed to keep this domain
        # module free from SQLAlchemy imports.  Infrastructure adapters expose
        # ``load_run_window_settings``.
        settings = self._loader.load_run_window_settings()
        return settings.is_run_window_open(now)
