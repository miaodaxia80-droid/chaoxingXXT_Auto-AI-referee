from __future__ import annotations

import hashlib
import hmac
import math
import threading
import time
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_MAX_FAILURES = 5
DEFAULT_WINDOW_SECONDS = 300
DEFAULT_BLOCK_SECONDS = 900
DEFAULT_MAX_BUCKETS = 10_000


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


@dataclass(slots=True)
class _Bucket:
    failures: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0


class LoginRateLimiter:
    """Bounded, process-local rate limiter for login failures."""

    def __init__(
        self,
        secret_key: bytes,
        *,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        block_seconds: int = DEFAULT_BLOCK_SECONDS,
        max_buckets: int = DEFAULT_MAX_BUCKETS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(secret_key, bytes) or not secret_key:
            raise ValueError("secret_key must be non-empty bytes")
        self._validate_positive_int(max_failures, "max_failures")
        self._validate_positive_int(window_seconds, "window_seconds")
        self._validate_positive_int(block_seconds, "block_seconds")
        self._validate_positive_int(max_buckets, "max_buckets")

        self._secret_key = secret_key
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._block_seconds = block_seconds
        self._max_buckets = max_buckets
        self._clock = clock
        self._buckets: dict[bytes, _Bucket] = {}
        self._lock = threading.Lock()

    @property
    def tracked_bucket_count(self) -> int:
        """Return the number of buckets currently retained in memory."""
        with self._lock:
            return len(self._buckets)

    def check(self, source: str, username: str) -> RateLimitDecision:
        """Check a login before password verification without changing its failure count."""
        bucket_key = self._bucket_key(source, username)
        with self._lock:
            now = self._now()
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                return self._allowed()

            blocked = self._blocked_decision(bucket, now)
            if blocked is not None:
                return blocked

            bucket.blocked_until = 0.0
            self._prune_failures(bucket, now)
            if not bucket.failures:
                self._buckets.pop(bucket_key, None)
            return self._allowed()

    def record_failure(self, source: str, username: str) -> RateLimitDecision:
        """Record a failed login and return whether another attempt is currently allowed."""
        bucket_key = self._bucket_key(source, username)
        with self._lock:
            now = self._now()
            bucket = self._buckets.get(bucket_key)
            if bucket is not None:
                blocked = self._blocked_decision(bucket, now)
                if blocked is not None:
                    return blocked
                bucket.blocked_until = 0.0
                self._prune_failures(bucket, now)
            else:
                self._make_room(now)
                bucket = _Bucket()
                self._buckets[bucket_key] = bucket

            bucket.failures.append(now)
            if len(bucket.failures) < self._max_failures:
                return self._allowed()

            bucket.failures.clear()
            bucket.blocked_until = now + self._block_seconds
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=self._block_seconds,
            )

    def clear(self, source: str, username: str) -> None:
        """Clear failures and any active block after a successful login."""
        bucket_key = self._bucket_key(source, username)
        with self._lock:
            self._buckets.pop(bucket_key, None)

    def _bucket_key(self, source: str, username: str) -> bytes:
        normalized_username = unicodedata.normalize(
            "NFKC",
            unicodedata.normalize("NFKC", username).strip().casefold(),
        )
        digest = hmac.new(self._secret_key, digestmod=hashlib.sha256)
        digest.update(b"chaoxing-login-rate-limit-v1\x00")
        for value in (source, normalized_username):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, byteorder="big"))
            digest.update(encoded)
        return digest.digest()

    def _now(self) -> float:
        now = self._clock()
        if not math.isfinite(now):
            raise ValueError("clock must return a finite value")
        return now

    def _blocked_decision(
        self,
        bucket: _Bucket,
        now: float,
    ) -> RateLimitDecision | None:
        remaining = bucket.blocked_until - now
        if remaining <= 0:
            return None
        retry_after = max(1, min(self._block_seconds, math.ceil(remaining)))
        return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)

    def _prune_failures(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self._window_seconds
        while bucket.failures and bucket.failures[0] <= cutoff:
            bucket.failures.popleft()

    def _make_room(self, now: float) -> None:
        if len(self._buckets) < self._max_buckets:
            return

        self._remove_expired(now)
        if len(self._buckets) < self._max_buckets:
            return

        victim_key = min(
            self._buckets,
            key=lambda key: self._eviction_priority(self._buckets[key], now),
        )
        del self._buckets[victim_key]

    def _remove_expired(self, now: float) -> None:
        for bucket_key, bucket in tuple(self._buckets.items()):
            if self._blocked_decision(bucket, now) is not None:
                continue
            bucket.blocked_until = 0.0
            self._prune_failures(bucket, now)
            if not bucket.failures:
                self._buckets.pop(bucket_key, None)

    def _eviction_priority(self, bucket: _Bucket, now: float) -> tuple[int, int, float]:
        if bucket.blocked_until > now:
            return (1, 0, bucket.blocked_until)
        last_failure = bucket.failures[-1] if bucket.failures else float("-inf")
        return (0, len(bucket.failures), last_failure)

    @staticmethod
    def _allowed() -> RateLimitDecision:
        return RateLimitDecision(allowed=True, retry_after_seconds=0)

    @staticmethod
    def _validate_positive_int(value: int, name: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
