from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest

from chaoxing_app.infrastructure.security.login_rate_limit import LoginRateLimiter


class FakeClock:
    def __init__(self, initial: float = 0.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_username_is_normalized_and_source_remains_part_of_bucket_key() -> None:
    limiter = LoginRateLimiter(b"test-secret", max_failures=2)

    assert limiter.record_failure("source-a", "  \uff21LICE  ").allowed
    decision = limiter.record_failure("source-a", "alice")

    assert not decision.allowed
    assert limiter.check("source-b", "alice").allowed


def test_structured_bucket_key_does_not_alias_adjacent_values() -> None:
    limiter = LoginRateLimiter(b"test-secret", max_failures=2)

    assert limiter.record_failure("a", "bc").allowed
    assert limiter.record_failure("ab", "c").allowed


def test_threshold_blocks_immediately_and_retry_after_is_bounded() -> None:
    clock = FakeClock(10.0)
    limiter = LoginRateLimiter(
        b"test-secret",
        max_failures=2,
        window_seconds=10,
        block_seconds=7,
        clock=clock,
    )

    assert limiter.check("source", "alice").retry_after_seconds == 0
    assert limiter.record_failure("source", "alice").allowed
    blocked = limiter.record_failure("source", "alice")

    assert not blocked.allowed
    assert blocked.retry_after_seconds == 7

    clock.advance(2.2)
    blocked = limiter.check("source", "alice")
    assert not blocked.allowed
    assert blocked.retry_after_seconds == 5

    clock.advance(4.8)
    assert limiter.check("source", "alice").allowed
    assert limiter.tracked_bucket_count == 0


def test_failures_outside_window_are_discarded() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(
        b"test-secret",
        max_failures=3,
        window_seconds=10,
        block_seconds=20,
        clock=clock,
    )

    assert limiter.record_failure("source", "alice").allowed
    assert limiter.record_failure("source", "alice").allowed
    clock.advance(10)

    assert limiter.record_failure("source", "alice").allowed
    assert limiter.record_failure("source", "alice").allowed
    assert not limiter.record_failure("source", "alice").allowed


def test_clear_removes_failures_and_active_block() -> None:
    limiter = LoginRateLimiter(b"test-secret", max_failures=1)
    assert not limiter.record_failure("source", "alice").allowed

    limiter.clear("source", "alice")

    assert limiter.check("source", "alice").allowed
    assert not limiter.record_failure("source", "alice").allowed


def test_capacity_is_bounded_and_expired_buckets_are_cleaned_lazily() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(
        b"test-secret",
        max_failures=3,
        window_seconds=10,
        max_buckets=2,
        clock=clock,
    )
    limiter.record_failure("source-1", "alice")
    limiter.record_failure("source-2", "alice")
    assert limiter.tracked_bucket_count == 2

    clock.advance(10)
    limiter.record_failure("source-3", "alice")

    assert limiter.tracked_bucket_count == 1

    limiter.record_failure("source-4", "alice")
    limiter.record_failure("source-5", "alice")
    assert limiter.tracked_bucket_count == 2


def test_bucket_storage_contains_digests_instead_of_login_identifiers() -> None:
    limiter = LoginRateLimiter(b"test-secret")
    limiter.record_failure("203.0.113.10", "DistinctiveUserName")

    retained_state = repr(vars(limiter))

    assert "203.0.113.10" not in retained_state
    assert "DistinctiveUserName" not in retained_state


def test_concurrent_failures_are_recorded_atomically() -> None:
    max_failures = 50
    limiter = LoginRateLimiter(b"test-secret", max_failures=max_failures)

    with ThreadPoolExecutor(max_workers=8) as executor:
        decisions = list(
            executor.map(
                lambda _: limiter.record_failure("source", "alice"),
                range(100),
            )
        )

    assert sum(decision.allowed for decision in decisions) == max_failures - 1
    assert not limiter.check("source", "alice").allowed
    assert limiter.tracked_bucket_count == 1


@pytest.mark.parametrize(
    "factory",
    [
        lambda: LoginRateLimiter(b"test-secret", max_failures=0),
        lambda: LoginRateLimiter(b"test-secret", window_seconds=0),
        lambda: LoginRateLimiter(b"test-secret", block_seconds=0),
        lambda: LoginRateLimiter(b"test-secret", max_buckets=0),
        lambda: LoginRateLimiter(b"test-secret", max_failures=True),
    ],
)
def test_positive_integer_configuration_is_required(
    factory: Callable[[], LoginRateLimiter],
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        factory()


def test_secret_key_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="secret_key"):
        LoginRateLimiter(b"")
