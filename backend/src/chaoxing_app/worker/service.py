from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from chaoxing_app.worker.supervisor import WorkerSupervisor

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SupervisorServiceConfig:
    poll_interval_seconds: float = 1.0
    shutdown_grace_seconds: float = 20.0

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        if self.shutdown_grace_seconds < 0:
            raise ValueError("shutdown grace must not be negative")


class SupervisorService:
    def __init__(
        self,
        *,
        supervisor: WorkerSupervisor,
        config: SupervisorServiceConfig | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        maintenance: Callable[[], object] | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._config = config or SupervisorServiceConfig()
        self._monotonic = monotonic
        self._maintenance = maintenance
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        return self._supervisor.active_task_ids

    @property
    def max_workers(self) -> int:
        return self._supervisor.max_workers

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("supervisor service already started")
        self._thread = threading.Thread(
            target=self._run,
            name="chaoxing-worker-supervisor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> tuple[str, ...]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._config.poll_interval_seconds + 2)

        self._supervisor.request_pause_all()
        deadline = self._monotonic() + self._config.shutdown_grace_seconds
        while self._supervisor.active_task_ids and self._monotonic() < deadline:
            self._supervisor.tick(claim_new=False)
            if self._supervisor.active_task_ids:
                time.sleep(min(self._config.poll_interval_seconds, 0.1))
        return self._supervisor.terminate_all()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._supervisor.tick()
                if self._maintenance is not None:
                    self._maintenance()
            except Exception:
                logger.exception("worker supervisor tick failed")
            self._stop.wait(self._config.poll_interval_seconds)
