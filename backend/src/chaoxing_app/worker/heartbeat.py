from __future__ import annotations

import os
import threading
from datetime import timedelta
from typing import Protocol

from sqlalchemy import Engine

from chaoxing_app.infrastructure.db.task_queue import (
    LeaseRenewal,
    TaskClaim,
    renew_task_lease,
)


class LeaseRenewer(Protocol):
    def __call__(
        self,
        engine: Engine,
        claim: TaskClaim,
        *,
        lease_duration: timedelta,
        process_id: int | None = None,
    ) -> LeaseRenewal | None: ...


class LeaseHeartbeat:
    def __init__(
        self,
        *,
        engine: Engine,
        claim: TaskClaim,
        interval: timedelta = timedelta(seconds=10),
        lease_duration: timedelta = timedelta(seconds=30),
        process_id: int | None = None,
        renewer: LeaseRenewer = renew_task_lease,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("heartbeat interval must be positive")
        if lease_duration <= interval:
            raise ValueError("lease duration must exceed the heartbeat interval")
        if process_id is not None and process_id <= 0:
            raise ValueError("process_id must be positive")
        self._engine = engine
        self._claim = claim
        self._interval = interval
        self._lease_duration = lease_duration
        self._process_id = process_id or os.getpid()
        self._renewer = renewer
        self._stop = threading.Event()
        self._lost_lease = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_renewal: LeaseRenewal | None = None

    @property
    def lost_lease(self) -> bool:
        return self._lost_lease.is_set()

    @property
    def last_renewal(self) -> LeaseRenewal | None:
        return self._last_renewal

    def pulse(self) -> bool:
        renewal = self._renewer(
            self._engine,
            self._claim,
            lease_duration=self._lease_duration,
            process_id=self._process_id,
        )
        self._last_renewal = renewal
        if renewal is None:
            self._lost_lease.set()
            self._stop.set()
            return False
        return True

    def start(self) -> bool:
        if self._thread is not None:
            raise RuntimeError("heartbeat already started")
        if not self.pulse():
            return False
        self._thread = threading.Thread(
            target=self._run,
            name=f"lease-heartbeat-{self._claim.run_id[:8]}",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self, *, timeout: float | None = None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        seconds = self._interval.total_seconds()
        while not self._stop.wait(seconds):
            if not self.pulse():
                return

    def __enter__(self) -> LeaseHeartbeat:
        if not self.start():
            raise RuntimeError("worker lost its lease before startup")
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.stop()
