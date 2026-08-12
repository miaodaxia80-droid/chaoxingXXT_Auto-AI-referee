from datetime import UTC, datetime

import pytest

from chaoxing_app.domain.settings import RunWindowSettings, is_run_window_open


def test_default_window_is_open_all_day() -> None:
    settings = RunWindowSettings()

    assert is_run_window_open(datetime(2026, 8, 10, 0, tzinfo=UTC), settings)
    assert is_run_window_open(datetime(2026, 8, 10, 23, 59, tzinfo=UTC), settings)


def test_disabled_window_means_unrestricted_but_disabled_worker_is_closed() -> None:
    unrestricted = RunWindowSettings(
        run_window_enabled=False,
        run_window_start="09:00",
        run_window_end="17:00",
    )
    stopped = RunWindowSettings(worker_enabled=False, run_window_enabled=False)

    assert is_run_window_open(datetime(2026, 8, 10, 20, tzinfo=UTC), unrestricted)
    assert not is_run_window_open(datetime(2026, 8, 10, 12, tzinfo=UTC), stopped)


def test_cross_midnight_window_uses_configured_timezone_and_half_open_boundaries() -> None:
    settings = RunWindowSettings(
        run_window_enabled=True,
        run_window_start="22:00",
        run_window_end="06:00",
        timezone="Asia/Shanghai",
    )

    # UTC+8: 14:00 UTC is the inclusive 22:00 opening boundary.
    assert is_run_window_open(datetime(2026, 8, 10, 14, tzinfo=UTC), settings)
    assert is_run_window_open(datetime(2026, 8, 10, 21, 59, tzinfo=UTC), settings)
    # UTC+8: 22:00 UTC is the exclusive 06:00 closing boundary.
    assert not is_run_window_open(datetime(2026, 8, 10, 22, tzinfo=UTC), settings)
    assert not is_run_window_open(datetime(2026, 8, 10, 13, 59, tzinfo=UTC), settings)


@pytest.mark.parametrize(
    "changes",
    [
        {"run_window_start": "9:00"},
        {"run_window_end": "24:00"},
        {"run_window_start": "09:00:00"},
        {"timezone": "Not/A-Timezone"},
    ],
)
def test_run_window_rejects_noncanonical_values(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RunWindowSettings(**changes)  # type: ignore[arg-type]
