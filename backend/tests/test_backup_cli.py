from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chaoxing_app import cli
from chaoxing_app.infrastructure.backup import (
    BackupResult,
    BackupValidationError,
    RestoreResult,
)


def test_parser_exposes_backup_and_restore_contracts_with_security_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = cli.build_parser()
    backup = parser.parse_args(["backup", "--output", "snapshot.zip", "--force"])
    restore = parser.parse_args(["restore", "--input", "snapshot.zip", "--force"])
    legacy = parser.parse_args(["import-legacy", "--source", "legacy.db"])
    serve = parser.parse_args(["serve", "--port", "6000"])

    assert backup.command == "backup"
    assert backup.output == Path("snapshot.zip")
    assert backup.force is True
    assert restore.command == "restore"
    assert restore.input == Path("snapshot.zip")
    assert restore.force is True
    assert legacy.command == "import-legacy"
    assert legacy.source == Path("legacy.db")
    assert legacy.apply is False
    assert serve.command == "serve"
    assert serve.port == 6000
    root_help = parser.format_help().casefold()
    assert "unencrypted" in root_help
    assert "highly sensitive" in root_help
    assert "stopped/offline" in root_help

    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["restore", "--help"])
    assert raised.value.code == 0
    restore_help = capsys.readouterr().out.casefold()
    assert "stopped/offline" in restore_help
    assert "completely" in restore_help
    assert "master key" in restore_help

    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["import-legacy", "--help"])
    assert raised.value.code == 0
    import_help = capsys.readouterr().out.casefold()
    assert "read-only" in import_help
    assert "dry-run" in import_help
    assert "plaintext credentials" in import_help


def test_serve_dispatch_disables_proxy_header_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(app: str, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    assert cli.run(["serve", "--host", "0.0.0.0", "--port", "6000", "--reload"]) == 0
    assert calls == [
        {
            "app": "chaoxing_app.main:app",
            "host": "0.0.0.0",
            "port": 6000,
            "reload": True,
            "proxy_headers": False,
        }
    ]


def test_backup_dispatch_uses_settings_paths_and_prints_sensitive_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        database_url="sqlite:///configured/custom.db",
        data_dir=tmp_path / "configured-data",
    )
    calls: list[dict[str, Any]] = []

    def fake_backup(**kwargs: Any) -> BackupResult:
        calls.append(kwargs)
        return BackupResult(
            archive_path=Path(kwargs["output_path"]).resolve(),
            database_path=tmp_path / "configured" / "custom.db",
            created_at_utc=datetime(2026, 8, 11, tzinfo=UTC),
        )

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_backup", fake_backup)
    output = tmp_path / "sensitive.zip"

    assert cli.run(["backup", "--output", str(output), "--force"]) == 0

    assert calls == [
        {
            "database_url": settings.database_url,
            "data_dir": settings.data_dir,
            "output_path": output,
            "force": True,
        }
    ]
    captured = capsys.readouterr()
    assert "highly sensitive" in captured.err.casefold()
    assert str(output.resolve()) in captured.out


def test_restore_dispatch_warns_before_call_and_does_not_require_a_live_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        database_url="sqlite:///configured/restore.db",
        data_dir=tmp_path / "configured-data",
    )
    calls: list[dict[str, Any]] = []
    rollback = settings.data_dir / "rollback-20260811T000000000000Z"

    def fake_restore(**kwargs: Any) -> RestoreResult:
        warning = capsys.readouterr().err.casefold()
        assert "offline" in warning
        assert "highly sensitive" in warning
        calls.append(kwargs)
        return RestoreResult(
            archive_path=Path(kwargs["input_path"]).resolve(),
            database_path=tmp_path / "configured" / "restore.db",
            master_key_path=settings.data_dir / "master.key",
            rollback_directory=rollback,
        )

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "restore_backup", fake_restore)
    input_path = tmp_path / "sensitive.zip"

    assert cli.run(["restore", "--input", str(input_path), "--force"]) == 0

    assert calls == [
        {
            "database_url": settings.database_url,
            "data_dir": settings.data_dir,
            "input_path": input_path,
            "force": True,
        }
    ]
    output = capsys.readouterr().out
    assert "Restore completed" in output
    assert str(rollback) in output
    assert "Keep the service offline" in output


def test_cli_reports_backup_errors_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        database_url="sqlite:///configured.db",
        data_dir=tmp_path,
    )

    def fail_backup(**_kwargs: Any) -> BackupResult:
        raise BackupValidationError("SQLite database failed integrity validation")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_backup", fail_backup)

    assert cli.run(["backup", "--output", str(tmp_path / "backup.zip")]) == 1
    captured = capsys.readouterr()
    assert "integrity validation" in captured.err
    assert "Traceback" not in captured.err


def test_legacy_import_cli_defaults_to_dry_run_and_redacts_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from chaoxing_app.infrastructure.legacy_import import (
        LegacyAccountResult,
        LegacyImportReport,
    )

    settings = SimpleNamespace(
        database_url="sqlite:///configured/target.db",
        data_dir=tmp_path / "configured-data",
    )
    calls: list[dict[str, Any]] = []

    def fake_import(**kwargs: Any) -> LegacyImportReport:
        calls.append(kwargs)
        return LegacyImportReport(
            applied=False,
            total_accounts=1,
            planned=1,
            created=0,
            duplicate=0,
            invalid=0,
            settings_mapped=2,
            warnings=("one unsupported setting was ignored",),
            results=(
                LegacyAccountResult(
                    row_id=7,
                    status="planned",
                    username_hint="138****8000",
                ),
            ),
        )

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "import_legacy_database", fake_import)
    source = tmp_path / "legacy.db"

    assert cli.run(["import-legacy", "--source", str(source)]) == 0
    assert calls == [
        {
            "source_path": source,
            "database_url": settings.database_url,
            "data_dir": settings.data_dir,
            "apply": False,
        }
    ]
    captured = capsys.readouterr()
    assert "DRY-RUN" in captured.out
    assert "138****8000" in captured.out
    assert "No target data was changed" in captured.out
    assert "plaintext credentials" in captured.err
    assert "unsupported setting" in captured.err


def test_legacy_import_cli_reports_errors_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from chaoxing_app.infrastructure.legacy_import import LegacyImportError

    settings = SimpleNamespace(
        database_url="sqlite:///configured/target.db",
        data_dir=tmp_path / "configured-data",
    )

    def fail_import(**_kwargs: Any) -> object:
        raise LegacyImportError("legacy source has an unsupported schema")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "import_legacy_database", fail_import)

    assert cli.run(["import-legacy", "--source", str(tmp_path / "legacy.db")]) == 1
    captured = capsys.readouterr()
    assert "unsupported schema" in captured.err
    assert "Traceback" not in captured.err
