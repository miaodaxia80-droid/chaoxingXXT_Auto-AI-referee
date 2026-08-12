from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

import chaoxing_app.main as app_main
import chaoxing_app.worker.process as worker_process
from alembic import command
from chaoxing_app.infrastructure.db.engine import DatabaseSchemaError
from chaoxing_app.infrastructure.db.task_queue import TaskClaim
from chaoxing_app.main import create_app
from chaoxing_app.settings import AppSettings
from chaoxing_app.worker.process import (
    MultiprocessingProcessHandle,
    SpawnProcessLauncher,
    WorkerProcessConfig,
)


def make_claim() -> TaskClaim:
    return TaskClaim(
        task_id="12345678-task",
        account_id=7,
        run_id="run-1",
        fencing_token=3,
        lease_expires_at=datetime(2026, 8, 10, tzinfo=UTC),
        owner_id="supervisor-1",
    )


def migrate_database(path: Path, revision: str = "head") -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    command.upgrade(config, revision)


def database_tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


@dataclass
class FakeChildProcess:
    pid: int | None = 4321
    exitcode: int | None = None
    started: bool = False
    terminated: bool = False
    join_timeouts: list[float | None] = field(default_factory=list)

    def start(self) -> None:
        self.started = True

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(timeout)

    def terminate(self) -> None:
        self.terminated = True


def test_launcher_passes_only_serializable_runtime_configuration() -> None:
    process = FakeChildProcess()
    recorded: dict[str, object] = {}

    def build_process(**kwargs: object) -> FakeChildProcess:
        recorded.update(kwargs)
        return process

    config = WorkerProcessConfig(
        database_url="sqlite:///worker.db",
        master_key_path=Path("private/master.key"),
    )
    handle = SpawnProcessLauncher(
        config=config,
        process_builder=build_process,  # type: ignore[arg-type]
    ).launch(make_claim())

    assert process.started is True
    assert handle.pid == 4321
    assert recorded["target"] is worker_process.worker_process_entry
    assert recorded["args"] == (config, make_claim())
    assert recorded["daemon"] is False
    assert recorded["name"] == "chaoxing-worker-12345678"
    assert "password" not in repr(recorded["args"]).casefold()
    assert "secret_box" not in repr(recorded["args"]).casefold()


def test_process_handle_polls_and_terminates_child() -> None:
    process = FakeChildProcess()
    handle = MultiprocessingProcessHandle(process)

    assert handle.poll() is None
    handle.terminate()
    assert process.terminated is True
    assert process.join_timeouts == [0]

    process.exitcode = 7
    assert handle.poll() == 7
    assert process.join_timeouts == [0, 0]


def test_default_builder_always_requests_spawn_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeChildProcess()
    requested_methods: list[str] = []
    process_kwargs: dict[str, object] = {}

    class FakeContext:
        def Process(self, **kwargs: object) -> FakeChildProcess:
            process_kwargs.update(kwargs)
            return process

    def get_context(method: str) -> FakeContext:
        requested_methods.append(method)
        return FakeContext()

    monkeypatch.setattr(worker_process.multiprocessing, "get_context", get_context)
    config = WorkerProcessConfig(
        database_url="sqlite:///worker.db",
        master_key_path=Path("master.key"),
    )

    built = worker_process._spawn_process_builder(
        target=worker_process.worker_process_entry,
        args=(config, make_claim()),
        name="worker",
        daemon=False,
    )

    assert built is process
    assert requested_methods == ["spawn"]
    assert process_kwargs["daemon"] is False


def test_worker_entry_rebuilds_process_local_resources_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    expected_result = object()

    class FakeEngine:
        def dispose(self) -> None:
            calls["disposed"] = True

    class FakeKeyStore:
        def __init__(self, path: Path) -> None:
            calls["key_path"] = path

        def load(self) -> bytes:
            calls["key_loaded"] = True
            return b"k" * 32

    class FakeSecretBox:
        def __init__(self, key: bytes) -> None:
            calls["secret_key"] = key

    class FakeAccountRuntimeFactory:
        def __init__(self, *, session_factory: object, secret_box: object) -> None:
            calls["account_runtime"] = self
            calls["session_factory"] = session_factory
            calls["secret_box"] = secret_box

    class FakeAnswerProviderRuntimeFactory:
        def __init__(self, *, session_factory: object, secret_box: object) -> None:
            calls["answer_provider_runtime"] = self
            calls["answer_session_factory"] = session_factory
            calls["answer_secret_box"] = secret_box

    class FakeRuntime:
        def __init__(self, **kwargs: object) -> None:
            calls["runtime_kwargs"] = kwargs

        def run(self) -> object:
            return expected_result

    engine = FakeEngine()
    executor = object()
    monkeypatch.setattr(worker_process, "create_database_engine", lambda _url: engine)
    monkeypatch.setattr(worker_process, "MasterKeyStore", FakeKeyStore)
    monkeypatch.setattr(worker_process, "SecretBox", FakeSecretBox)
    monkeypatch.setattr(worker_process, "make_session_factory", lambda _engine: "sessions")
    monkeypatch.setattr(
        worker_process,
        "AccountRuntimeFactory",
        FakeAccountRuntimeFactory,
    )
    monkeypatch.setattr(
        worker_process,
        "AnswerProviderRuntimeFactory",
        FakeAnswerProviderRuntimeFactory,
    )

    def build_executor(**kwargs: object) -> object:
        calls["executor_kwargs"] = kwargs
        return executor

    monkeypatch.setattr(worker_process, "_build_task_executor", build_executor)
    monkeypatch.setattr(worker_process, "WorkerRuntime", FakeRuntime)
    config = WorkerProcessConfig(
        database_url="sqlite:///worker.db",
        master_key_path=Path("data/master.key"),
        heartbeat_interval_seconds=4,
        lease_duration_seconds=12,
    )

    result = worker_process.worker_process_entry(config, make_claim())

    assert result is expected_result
    assert calls["key_path"] == Path("data/master.key")
    assert calls["key_loaded"] is True
    assert calls["session_factory"] == "sessions"
    assert calls["answer_session_factory"] == "sessions"
    assert calls["answer_secret_box"] is calls["secret_box"]
    assert calls["executor_kwargs"] == {
        "engine": engine,
        "account_runtime": calls["account_runtime"],
        "answer_provider_runtime": calls["answer_provider_runtime"],
    }
    runtime_kwargs = calls["runtime_kwargs"]
    assert isinstance(runtime_kwargs, dict)
    assert runtime_kwargs["engine"] is engine
    assert runtime_kwargs["claim"] == make_claim()
    assert runtime_kwargs["executor"] is executor
    assert runtime_kwargs["heartbeat_interval"].total_seconds() == 4  # type: ignore[union-attr]
    assert runtime_kwargs["lease_duration"].total_seconds() == 12  # type: ignore[union-attr]
    assert calls["disposed"] is True


@pytest.mark.parametrize(
    ("heartbeat", "lease"),
    [(0, 30), (10, 10), (31, 30)],
)
def test_worker_timing_requires_heartbeat_before_lease(
    heartbeat: float,
    lease: float,
) -> None:
    with pytest.raises(ValueError):
        AppSettings(
            worker_heartbeat_interval_seconds=heartbeat,
            worker_lease_duration_seconds=lease,
        )


def test_test_environment_does_not_build_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_build(**_kwargs: object) -> object:
        raise AssertionError("test environment must not build the supervisor")

    monkeypatch.setattr(app_main, "build_supervisor_service", unexpected_build)
    settings = AppSettings(
        environment="test",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
    )
    app = create_app(settings)

    with TestClient(app):
        assert app.state.supervisor_service is None

    assert "accounts" in database_tables(tmp_path / "test.db")
    assert "alembic_version" not in database_tables(tmp_path / "test.db")


def test_development_startup_rejects_unmigrated_database_without_creating_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "blank.db"
    data_dir = tmp_path / "data"
    app = create_app(
        AppSettings(
            environment="development",
            data_dir=data_dir,
            database_url=f"sqlite:///{database_path.as_posix()}",
        )
    )

    with pytest.raises(DatabaseSchemaError, match="not initialized by Alembic"), TestClient(
        app
    ):
        pass

    assert database_tables(database_path) == set()
    assert not (data_dir / "master.key").exists()


def test_development_startup_rejects_outdated_database_without_creating_new_tables(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "outdated.db"
    data_dir = tmp_path / "data"
    migrate_database(database_path, "20260812_0002")
    tables_before = database_tables(database_path)
    app = create_app(
        AppSettings(
            environment="development",
            data_dir=data_dir,
            database_url=f"sqlite:///{database_path.as_posix()}",
        )
    )

    with pytest.raises(DatabaseSchemaError, match="20260812_0002"), TestClient(app):
        pass

    assert database_tables(database_path) == tables_before
    assert "manual_intervention_resolutions" not in tables_before
    assert not (data_dir / "master.key").exists()


def test_lifespan_stops_supervisor_before_disposing_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    create_engine = app_main.create_database_engine

    def recording_engine(database_url: str):
        engine = create_engine(database_url)
        dispose = engine.dispose

        def recording_dispose() -> None:
            events.append("dispose")
            dispose()

        engine.dispose = recording_dispose
        return engine

    class RecordingService:
        def start(self) -> None:
            events.append("start")

        def stop(self) -> tuple[str, ...]:
            events.append("stop")
            return ()

    monkeypatch.setattr(app_main, "create_database_engine", recording_engine)
    monkeypatch.setattr(
        app_main,
        "build_supervisor_service",
        lambda **_kwargs: RecordingService(),
    )
    settings = AppSettings(
        environment="development",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}",
    )
    migrate_database(tmp_path / "app.db")
    app = create_app(settings)

    with TestClient(app):
        assert events == ["start"]
        assert "manual_intervention_resolutions" in database_tables(tmp_path / "app.db")

    assert events == ["start", "stop", "dispose"]
