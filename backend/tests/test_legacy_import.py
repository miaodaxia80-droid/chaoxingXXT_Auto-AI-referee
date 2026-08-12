from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.engine import create_database_engine, create_schema
from chaoxing_app.infrastructure.db.models import Account, IntegrationSetting, SystemSettings
from chaoxing_app.infrastructure.legacy_import import (
    LegacyImportError,
    import_legacy_database,
)
from chaoxing_app.infrastructure.security.secrets import MasterKeyStore, SecretBox

_LEGACY_SCHEMA = """
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT,
  use_cookies INTEGER DEFAULT 0,
  cookies_data TEXT,
  speed REAL DEFAULT 1.0,
  jobs INTEGER DEFAULT 4,
  notopen_action TEXT DEFAULT 'retry',
  tiku_config TEXT DEFAULT '{}',
  notification_config TEXT DEFAULT '{}',
  enabled INTEGER DEFAULT 1,
  remark TEXT DEFAULT '',
  user_agent TEXT DEFAULT '',
  created_at TEXT
);
CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def _create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(_LEGACY_SCHEMA)
        connection.executemany(
            "INSERT INTO users "
            "(username, password, use_cookies, cookies_data, speed, jobs, notopen_action, "
            "tiku_config, notification_config, enabled, remark, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "13800138000",
                    "legacy-password",
                    1,
                    "uid=1001; token=secret-cookie",
                    1.5,
                    4,
                    "continue",
                    '{"provider":"legacy"}',
                    "{}",
                    0,
                    "Imported account",
                    "Legacy browser",
                ),
                (
                    "second-user",
                    "second-password",
                    0,
                    "uid=stale; token=must-not-import",
                    1.0,
                    2,
                    "retry",
                    "{}",
                    '{"serverchan":"legacy-secret"}',
                    1,
                    "",
                    "",
                ),
                (
                    "invalid-speed",
                    "must-not-leak",
                    0,
                    None,
                    9.0,
                    1,
                    "retry",
                    "{}",
                    "{}",
                    1,
                    "",
                    "",
                ),
                (
                    "ask-policy-user",
                    "ask-password",
                    0,
                    None,
                    1.0,
                    1,
                    "ask",
                    "{}",
                    "{}",
                    1,
                    "",
                    "",
                ),
            ],
        )
        connection.executemany(
            'INSERT INTO settings ("key", value) VALUES (?, ?)',
            [
                ("scheduler_paused", "true"),
                ("run_window_enabled", "true"),
                ("run_window_start", "08:15"),
                ("run_window_end", "22:30"),
                ("timezone", "Asia/Shanghai"),
                ("show_system_metrics", "true"),
            ],
        )


def _create_target_database(path: Path) -> str:
    database_url = f"sqlite:///{path.as_posix()}"
    engine = create_database_engine(database_url)
    create_schema(engine)
    engine.dispose()
    return database_url


def _account_count(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("SELECT count(*) FROM accounts").fetchone()[0])


def test_dry_run_is_read_only_and_does_not_create_a_master_key(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    data_dir = tmp_path / "target-data"
    _create_legacy_database(source)
    database_url = _create_target_database(target)
    source_before = source.read_bytes()

    report = import_legacy_database(
        source_path=source,
        database_url=database_url,
        data_dir=data_dir,
    )

    assert report.applied is False
    assert report.total_accounts == 4
    assert report.planned == 3
    assert report.created == 0
    assert report.invalid == 1
    assert report.settings_mapped == 5
    assert any("interactive unopened-chapter" in warning for warning in report.warnings)
    assert _account_count(target) == 0
    assert not (data_dir / "master.key").exists()
    assert source.read_bytes() == source_before
    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()


def test_apply_encrypts_supported_accounts_and_maps_scheduler_settings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    data_dir = tmp_path / "target-data"
    _create_legacy_database(source)
    database_url = _create_target_database(target)

    report = import_legacy_database(
        source_path=source,
        database_url=database_url,
        data_dir=data_dir,
        apply=True,
    )

    assert report.applied is True
    assert report.created == 3
    assert report.invalid == 1
    assert report.duplicate == 0
    assert any("answer configuration" in warning for warning in report.warnings)
    assert any("notification configuration" in warning for warning in report.warnings)
    assert any("unsupported legacy setting" in warning for warning in report.warnings)
    assert all("legacy-password" not in warning for warning in report.warnings)
    assert all("secret-cookie" not in warning for warning in report.warnings)

    key = MasterKeyStore(data_dir / "master.key").load()
    secret_box = SecretBox(key)
    engine = create_database_engine(database_url)
    try:
        with Session(engine) as session:
            accounts = list(session.scalars(select(Account).order_by(Account.id)))
            assert len(accounts) == 3
            first, second, third = accounts
            assert secret_box.decrypt(
                first.secret.username_encrypted,
                purpose=f"account:{first.id}:username",
            ) == "13800138000"
            assert secret_box.decrypt(
                first.secret.password_encrypted or "",
                purpose=f"account:{first.id}:password",
            ) == "legacy-password"
            assert secret_box.decrypt(
                first.secret.cookies_encrypted or "",
                purpose=f"account:{first.id}:cookies",
            ) == "uid=1001; token=secret-cookie"
            assert first.enabled is False
            assert first.speed == 1.5
            assert first.chapter_concurrency == 4
            assert first.unopened_policy == "skip"
            assert second.enabled is True
            assert second.unopened_policy == "retry"
            assert second.secret.cookies_encrypted is None
            assert third.unopened_policy == "retry"

            settings = session.get(SystemSettings, 1)
            assert settings is not None
            assert settings.worker_enabled is False
            assert settings.run_window_enabled is True
            assert settings.run_window_start == "08:15"
            assert settings.run_window_end == "22:30"
            assert settings.timezone == "Asia/Shanghai"
    finally:
        engine.dispose()


def test_apply_preserves_password_whitespace(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    data_dir = tmp_path / "target-data"
    _create_legacy_database(source)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE users SET password = ? WHERE username = ?",
            ("  password with spaces  ", "13800138000"),
        )
    database_url = _create_target_database(target)

    report = import_legacy_database(
        source_path=source,
        database_url=database_url,
        data_dir=data_dir,
        apply=True,
    )

    assert report.created == 3
    key = MasterKeyStore(data_dir / "master.key").load()
    engine = create_database_engine(database_url)
    try:
        with Session(engine) as session:
            account = session.scalar(select(Account).where(Account.username_hint == "138****8000"))
            assert account is not None
            assert SecretBox(key).decrypt(
                account.secret.password_encrypted or "",
                purpose=f"account:{account.id}:password",
            ) == "  password with spaces  "
    finally:
        engine.dispose()


def test_import_rejects_master_key_mismatch_for_integration_secrets(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    data_dir = tmp_path / "target-data"
    _create_legacy_database(source)
    database_url = _create_target_database(target)

    original_key = MasterKeyStore(data_dir / "master.key").load_or_create()
    engine = create_database_engine(database_url)
    try:
        with Session(engine) as session:
            session.add(
                IntegrationSetting(
                    kind="answer",
                    enabled=False,
                    public_config={},
                    secret_config_encrypted=SecretBox(original_key).encrypt(
                        '{"answer.tokens":"private"}',
                        purpose="integration:answer:secrets",
                    ),
                )
            )
            session.commit()
    finally:
        engine.dispose()

    replacement_key = MasterKeyStore(tmp_path / "replacement.key").load_or_create()
    (data_dir / "master.key").write_bytes((tmp_path / "replacement.key").read_bytes())
    assert replacement_key != original_key

    with pytest.raises(LegacyImportError, match="secrets do not match"):
        import_legacy_database(
            source_path=source,
            database_url=database_url,
            data_dir=data_dir,
            apply=True,
        )


def test_repeated_apply_skips_existing_accounts_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    data_dir = tmp_path / "target-data"
    _create_legacy_database(source)
    database_url = _create_target_database(target)

    first = import_legacy_database(
        source_path=source,
        database_url=database_url,
        data_dir=data_dir,
        apply=True,
    )
    second = import_legacy_database(
        source_path=source,
        database_url=database_url,
        data_dir=data_dir,
        apply=True,
    )

    assert first.created == 3
    assert second.created == 0
    assert second.duplicate == 3
    assert second.invalid == 1
    assert second.settings_mapped == 0
    assert any("target already has accounts" in warning for warning in second.warnings)
    assert _account_count(target) == 3


def test_import_rejects_unsupported_source_schema_and_same_file(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    data_dir = tmp_path / "data"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    database_url = _create_target_database(target)

    with pytest.raises(LegacyImportError, match="unsupported users schema"):
        import_legacy_database(
            source_path=source,
            database_url=database_url,
            data_dir=data_dir,
        )

    with pytest.raises(LegacyImportError, match="must be different"):
        import_legacy_database(
            source_path=target,
            database_url=database_url,
            data_dir=data_dir,
        )
