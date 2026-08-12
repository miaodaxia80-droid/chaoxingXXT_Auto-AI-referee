from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from math import isfinite
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine

from chaoxing_app.application.account_runtime import AccountRuntimeFactory
from chaoxing_app.application.answer_runtime import AnswerProviderRuntimeFactory
from chaoxing_app.infrastructure.db.engine import (
    create_database_engine,
    make_session_factory,
)
from chaoxing_app.infrastructure.db.task_queue import TaskClaim
from chaoxing_app.infrastructure.security.secrets import MasterKeyStore, SecretBox
from chaoxing_app.worker.runtime import TaskExecutor, WorkerResult, WorkerRuntime


@dataclass(frozen=True, slots=True)
class WorkerProcessConfig:
    database_url: str
    master_key_path: Path
    heartbeat_interval_seconds: float = 10.0
    lease_duration_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.database_url.strip():
            raise ValueError("database URL must not be blank")
        if not isfinite(self.heartbeat_interval_seconds):
            raise ValueError("heartbeat interval must be finite")
        if not isfinite(self.lease_duration_seconds):
            raise ValueError("lease duration must be finite")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        if self.lease_duration_seconds <= self.heartbeat_interval_seconds:
            raise ValueError("lease duration must exceed the heartbeat interval")


class ChildProcess(Protocol):
    @property
    def pid(self) -> int | None: ...

    @property
    def exitcode(self) -> int | None: ...

    def start(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def terminate(self) -> None: ...


ProcessTarget = Callable[[WorkerProcessConfig, TaskClaim], object]


class ProcessBuilder(Protocol):
    def __call__(
        self,
        *,
        target: ProcessTarget,
        args: tuple[WorkerProcessConfig, TaskClaim],
        name: str,
        daemon: bool,
    ) -> ChildProcess: ...


def _spawn_process_builder(
    *,
    target: ProcessTarget,
    args: tuple[WorkerProcessConfig, TaskClaim],
    name: str,
    daemon: bool,
) -> ChildProcess:
    context = multiprocessing.get_context("spawn")
    return context.Process(target=target, args=args, name=name, daemon=daemon)


class MultiprocessingProcessHandle:
    def __init__(self, process: ChildProcess) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        pid = self._process.pid
        if pid is None or pid <= 0:
            raise RuntimeError("worker process has no valid process id")
        return pid

    def poll(self) -> int | None:
        exit_code = self._process.exitcode
        if exit_code is not None:
            self._process.join(timeout=0)
        return exit_code

    def terminate(self) -> None:
        if self._process.exitcode is None:
            self._process.terminate()
        self._process.join(timeout=0)


class SpawnProcessLauncher:
    def __init__(
        self,
        *,
        config: WorkerProcessConfig,
        process_builder: ProcessBuilder = _spawn_process_builder,
    ) -> None:
        self._config = config
        self._process_builder = process_builder

    def launch(self, claim: TaskClaim) -> MultiprocessingProcessHandle:
        process = self._process_builder(
            target=worker_process_entry,
            args=(self._config, claim),
            name=f"chaoxing-worker-{claim.task_id[:8]}",
            daemon=False,
        )
        process.start()
        handle = MultiprocessingProcessHandle(process)
        try:
            _ = handle.pid
        except BaseException:
            handle.terminate()
            raise
        return handle


def _build_task_executor(
    *,
    engine: Engine,
    account_runtime: AccountRuntimeFactory,
    answer_provider_runtime: AnswerProviderRuntimeFactory,
) -> TaskExecutor:
    from chaoxing_app.worker.study_executor import StudyTaskExecutor

    return StudyTaskExecutor(
        engine=engine,
        account_runtime=account_runtime,
        answer_provider_runtime=answer_provider_runtime,
    )


def worker_process_entry(
    config: WorkerProcessConfig,
    claim: TaskClaim,
) -> WorkerResult:
    """Rebuild all process-local resources and execute one fenced task claim."""
    engine = create_database_engine(config.database_url)
    try:
        master_key = MasterKeyStore(config.master_key_path).load()
        session_factory = make_session_factory(engine)
        secret_box = SecretBox(master_key)
        runtime_factory = AccountRuntimeFactory(
            session_factory=session_factory,
            secret_box=secret_box,
        )
        answer_provider_runtime = AnswerProviderRuntimeFactory(
            session_factory=session_factory,
            secret_box=secret_box,
        )
        executor = _build_task_executor(
            engine=engine,
            account_runtime=runtime_factory,
            answer_provider_runtime=answer_provider_runtime,
        )
        runtime = WorkerRuntime(
            engine=engine,
            claim=claim,
            executor=executor,
            heartbeat_interval=timedelta(seconds=config.heartbeat_interval_seconds),
            lease_duration=timedelta(seconds=config.lease_duration_seconds),
        )
        return runtime.run()
    finally:
        engine.dispose()
