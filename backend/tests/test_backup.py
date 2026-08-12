from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from chaoxing_app.infrastructure import backup as backup_module
from chaoxing_app.infrastructure.backup import (
    BackupDestinationExistsError,
    BackupValidationError,
    RestoreOperationError,
    RestoreRollbackError,
    RestoreTargetExistsError,
    UnsupportedDatabaseError,
    create_backup,
    restore_backup,
    sqlite_database_path,
)
from chaoxing_app.infrastructure.security.secrets import MasterKeyStore, SecretBox

FIXED_TIME = datetime(2026, 8, 11, 1, 2, 3, 456789, tzinfo=UTC)


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def create_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE fixture (value TEXT NOT NULL)")
        connection.execute("INSERT INTO fixture (value) VALUES (?)", (value,))
        connection.commit()


def read_database(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        value = connection.execute("SELECT value FROM fixture").fetchone()
    assert value is not None
    return str(value[0])


def create_key(data_dir: Path) -> bytes:
    key_path = data_dir / "master.key"
    MasterKeyStore(key_path).load_or_create()
    return key_path.read_bytes()


def make_backup(root: Path, *, value: str = "backup-data") -> tuple[Path, bytes]:
    data_dir = root / "source-data"
    database_path = root / "source-db" / "custom-source.sqlite"
    create_database(database_path, value)
    key_bytes = create_key(data_dir)
    archive_path = root / "archives" / "snapshot.cxbackup"
    create_backup(
        database_url=database_url(database_path),
        data_dir=data_dir,
        output_path=archive_path,
        now=lambda: FIXED_TIME,
    )
    return archive_path, key_bytes


def archive_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def write_archive(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_backup_uses_snapshot_api_and_writes_exact_validated_archive(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database_path = tmp_path / "databases" / "not-the-default-name.sqlite"
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE fixture (value TEXT NOT NULL)")
        connection.execute("INSERT INTO fixture (value) VALUES ('committed-in-wal')")
        connection.commit()
        key_bytes = create_key(data_dir)
        output = tmp_path / "nested" / "backup.zip"

        result = create_backup(
            database_url=database_url(database_path),
            data_dir=data_dir,
            output_path=output,
            now=lambda: FIXED_TIME,
        )
    finally:
        connection.close()

    assert result.archive_path == output.resolve()
    assert result.database_path == database_path.resolve()
    assert result.created_at_utc == FIXED_TIME
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["manifest.json", "database.sqlite3", "master.key"]
        manifest = json.loads(archive.read("manifest.json"))
        database_bytes = archive.read("database.sqlite3")
        archived_key = archive.read("master.key")
    assert manifest == {
        "format": "chaoxing-app-backup",
        "version": 1,
        "created_at_utc": FIXED_TIME.isoformat(),
        "database_sha256": hashlib.sha256(database_bytes).hexdigest(),
        "master_key_sha256": hashlib.sha256(archived_key).hexdigest(),
    }
    assert archived_key == key_bytes
    extracted = tmp_path / "extracted.sqlite"
    extracted.write_bytes(database_bytes)
    assert read_database(extracted) == "committed-in-wal"
    with closing(sqlite3.connect(extracted)) as snapshot:
        assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_backup_refuses_existing_output_unless_force_is_explicit(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database_path = tmp_path / "private.sqlite"
    create_database(database_path, "fixture")
    create_key(data_dir)
    output = tmp_path / "backup.zip"
    output.write_bytes(b"existing-backup-must-survive")

    with pytest.raises(BackupDestinationExistsError):
        create_backup(
            database_url=database_url(database_path),
            data_dir=data_dir,
            output_path=output,
        )
    assert output.read_bytes() == b"existing-backup-must-survive"

    create_backup(
        database_url=database_url(database_path),
        data_dir=data_dir,
        output_path=output,
        force=True,
    )
    assert zipfile.is_zipfile(output)

    directory_output = tmp_path / "existing-directory"
    directory_output.mkdir()
    with pytest.raises(BackupDestinationExistsError):
        create_backup(
            database_url=database_url(database_path),
            data_dir=data_dir,
            output_path=directory_output,
            force=True,
        )

    with pytest.raises(BackupValidationError, match="live application data"):
        create_backup(
            database_url=database_url(database_path),
            data_dir=data_dir,
            output_path=database_path,
            force=True,
        )
    assert read_database(database_path) == "fixture"


def test_database_url_parser_supports_custom_relative_names_and_rejects_non_files(
    tmp_path: Path,
) -> None:
    assert (
        sqlite_database_path(
            "sqlite:///nested/custom.sqlite",
            base_directory=tmp_path,
        )
        == (tmp_path / "nested" / "custom.sqlite").resolve()
    )

    for database_url_value in [
        "postgresql://localhost/private",
        "sqlite:///:memory:",
        "sqlite://",
        "sqlite:///file:private.sqlite",
    ]:
        with pytest.raises(UnsupportedDatabaseError):
            sqlite_database_path(database_url_value)


def test_backup_validates_source_database_and_existing_master_key(tmp_path: Path) -> None:
    database_path = tmp_path / "database.sqlite"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path.write_bytes(b"not a sqlite database")
    (data_dir / "master.key").write_text("not-a-key", encoding="ascii")

    with pytest.raises(BackupValidationError, match="master key"):
        create_backup(
            database_url=database_url(database_path),
            data_dir=data_dir,
            output_path=tmp_path / "backup.zip",
        )

    (data_dir / "master.key").unlink()
    create_key(data_dir)
    with pytest.raises(BackupValidationError, match="SQLite"):
        create_backup(
            database_url=database_url(database_path),
            data_dir=data_dir,
            output_path=tmp_path / "backup.zip",
        )


def test_backup_validates_integration_secrets_against_master_key(tmp_path: Path) -> None:
    database_path = tmp_path / "database.sqlite"
    data_dir = tmp_path / "data"
    create_database(database_path, "fixture")
    create_key(data_dir)
    secret_box = SecretBox(MasterKeyStore(data_dir / "master.key").load())
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "CREATE TABLE integration_settings (kind TEXT NOT NULL, secret_config_encrypted TEXT)"
        )
        connection.execute(
            "INSERT INTO integration_settings (kind, secret_config_encrypted) VALUES (?, ?)",
            (
                "answer-openai",
                secret_box.encrypt(
                    '{"api_key":"secret"}',
                    purpose="integration:answer-openai:secrets",
                ),
            ),
        )
        connection.commit()

    create_backup(
        database_url=database_url(database_path),
        data_dir=data_dir,
        output_path=tmp_path / "matching.zip",
    )
    assert zipfile.is_zipfile(tmp_path / "matching.zip")

    unrelated_key = create_key(tmp_path / "unrelated-key")
    (data_dir / "master.key").write_bytes(unrelated_key)
    with pytest.raises(BackupValidationError, match="do not match"):
        create_backup(
            database_url=database_url(database_path),
            data_dir=data_dir,
            output_path=tmp_path / "mismatched.zip",
        )
    assert not (tmp_path / "mismatched.zip").exists()


def test_restore_to_empty_target_uses_configured_database_filename(tmp_path: Path) -> None:
    archive_path, archived_key = make_backup(tmp_path / "backup")
    target_data = tmp_path / "target-data"
    target_database = tmp_path / "different-location" / "restored-name.db"

    result = restore_backup(
        database_url=database_url(target_database),
        data_dir=target_data,
        input_path=archive_path,
    )

    assert result.database_path == target_database.resolve()
    assert result.rollback_directory is None
    assert read_database(target_database) == "backup-data"
    assert (target_data / "master.key").read_bytes() == archived_key
    assert not (target_data / ".restore.lock").exists()


def test_restore_refuses_existing_lock_without_touching_live_data(tmp_path: Path) -> None:
    archive_path, _archived_key = make_backup(tmp_path / "backup")
    target_data = tmp_path / "target-data"
    target_data.mkdir()
    target_database = tmp_path / "target.sqlite"
    create_database(target_database, "live-data")
    live_key = create_key(target_data)
    lock_path = target_data / ".restore.lock"
    lock_path.write_text('{"pid": 123}', encoding="utf-8")

    with pytest.raises(RestoreOperationError, match="another restore"):
        restore_backup(
            database_url=database_url(target_database),
            data_dir=target_data,
            input_path=archive_path,
            force=True,
        )

    assert read_database(target_database) == "live-data"
    assert (target_data / "master.key").read_bytes() == live_key
    assert lock_path.exists()


def test_restore_refuses_any_existing_live_data_without_force(tmp_path: Path) -> None:
    archive_path, _archived_key = make_backup(tmp_path / "backup")
    target_data = tmp_path / "target-data"
    target_database = tmp_path / "target.sqlite"
    create_database(target_database, "live-data")
    live_key = create_key(target_data)

    with pytest.raises(RestoreTargetExistsError, match="--force"):
        restore_backup(
            database_url=database_url(target_database),
            data_dir=target_data,
            input_path=archive_path,
        )

    assert read_database(target_database) == "live-data"
    assert (target_data / "master.key").read_bytes() == live_key


def test_restore_refuses_orphan_rollback_journal_without_force(tmp_path: Path) -> None:
    archive_path, _archived_key = make_backup(tmp_path / "backup")
    target_database = tmp_path / "target.sqlite"
    journal_path = Path(f"{target_database}-journal")
    journal_path.write_bytes(b"orphan-hot-journal")

    with pytest.raises(RestoreTargetExistsError, match="--force"):
        restore_backup(
            database_url=database_url(target_database),
            data_dir=tmp_path / "target-data",
            input_path=archive_path,
        )

    assert journal_path.read_bytes() == b"orphan-hot-journal"
    assert not target_database.exists()


def test_forced_restore_moves_database_key_and_sqlite_sidecars_into_rollback(
    tmp_path: Path,
) -> None:
    archive_path, archived_key = make_backup(tmp_path / "backup", value="restored-data")
    target_data = tmp_path / "target-data"
    target_database = tmp_path / "live" / "custom-live.sqlite"
    create_database(target_database, "original-live-data")
    original_key = create_key(target_data)
    wal_path = Path(f"{target_database}-wal")
    shm_path = Path(f"{target_database}-shm")
    journal_path = Path(f"{target_database}-journal")
    wal_path.write_bytes(b"original-wal")
    shm_path.write_bytes(b"original-shm")
    journal_path.write_bytes(b"original-journal")

    result = restore_backup(
        database_url=database_url(target_database),
        data_dir=target_data,
        input_path=archive_path,
        force=True,
        now=lambda: FIXED_TIME,
    )

    rollback = result.rollback_directory
    assert rollback is not None
    assert rollback.parent == target_data.resolve()
    assert rollback.name == "rollback-20260811T010203456789Z"
    assert read_database(target_database) == "restored-data"
    assert (target_data / "master.key").read_bytes() == archived_key
    assert (rollback / wal_path.name).read_bytes() == b"original-wal"
    assert (rollback / shm_path.name).read_bytes() == b"original-shm"
    assert (rollback / journal_path.name).read_bytes() == b"original-journal"
    assert read_database(rollback / target_database.name) == "original-live-data"
    assert (rollback / "master.key").read_bytes() == original_key
    rollback_manifest = json.loads((rollback / "rollback-manifest.json").read_text())
    assert {item["role"] for item in rollback_manifest["originals"]} == {
        "database",
        "master-key",
        "database-wal",
        "database-shm",
        "database-journal",
    }


def test_restore_rejects_database_member_above_configured_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, _key = make_backup(tmp_path / "backup")
    monkeypatch.setattr(backup_module, "_MAX_DATABASE_BYTES", 1)
    target_database = tmp_path / "target.sqlite"
    target_data = tmp_path / "target-data"

    with pytest.raises(BackupValidationError, match="database member is too large"):
        restore_backup(
            database_url=database_url(target_database),
            data_dir=target_data,
            input_path=archive_path,
        )

    assert not target_database.exists()
    assert not (target_data / "master.key").exists()


def test_member_extraction_enforces_limit_while_streaming(tmp_path: Path) -> None:
    class FakeArchive:
        @staticmethod
        def open(_member: zipfile.ZipInfo, mode: str = "r") -> BytesIO:
            assert mode == "r"
            return BytesIO(b"0123456789")

    destination = tmp_path / "staged-member"
    with pytest.raises(BackupValidationError, match="archive member is too large"):
        backup_module._extract_member(
            FakeArchive(),  # type: ignore[arg-type]
            zipfile.ZipInfo("database.sqlite3"),
            destination,
            max_bytes=4,
        )

    assert destination.stat().st_size == 0


def test_failed_install_quarantines_new_file_and_returns_originals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, _archived_key = make_backup(tmp_path / "backup", value="new-data")
    target_data = tmp_path / "target-data"
    target_database = tmp_path / "live.sqlite"
    create_database(target_database, "original-data")
    original_key = create_key(target_data)
    real_replace = os.replace

    def fail_installing_staged_key(source: Path, destination: Path) -> None:
        if source.name.startswith(".restore-master-key-") and destination.name == "master.key":
            raise OSError("simulated key install failure")
        real_replace(source, destination)

    monkeypatch.setattr(backup_module, "_replace", fail_installing_staged_key)

    with pytest.raises(RestoreOperationError, match="original data was returned"):
        restore_backup(
            database_url=database_url(target_database),
            data_dir=target_data,
            input_path=archive_path,
            force=True,
            now=lambda: FIXED_TIME,
        )

    assert read_database(target_database) == "original-data"
    assert (target_data / "master.key").read_bytes() == original_key
    rollback = target_data / "rollback-20260811T010203456789Z"
    quarantined = rollback / "failed-restored-database"
    assert read_database(quarantined) == "new-data"
    assert archive_path.exists()


@pytest.mark.parametrize("extra_name", ["../outside", "/absolute", "extra.txt"])
def test_restore_rejects_path_traversal_and_extra_archive_members(
    tmp_path: Path,
    extra_name: str,
) -> None:
    archive_path, _key = make_backup(tmp_path / "backup")
    malicious = tmp_path / "malicious.zip"
    members = archive_members(archive_path)
    members[extra_name] = b"untrusted"
    write_archive(malicious, members)
    target_database = tmp_path / "target.sqlite"
    target_data = tmp_path / "target-data"

    with pytest.raises(BackupValidationError, match="contain only"):
        restore_backup(
            database_url=database_url(target_database),
            data_dir=target_data,
            input_path=malicious,
        )

    assert not target_database.exists()
    assert not (target_data / "master.key").exists()
    assert not (tmp_path / "outside").exists()


def test_restore_rejects_digest_mismatch_invalid_key_and_corrupt_database(
    tmp_path: Path,
) -> None:
    archive_path, _key = make_backup(tmp_path / "backup")
    original = archive_members(archive_path)
    scenarios: list[tuple[str, bytes, str]] = [
        ("master.key", b"invalid-key", "master key"),
        ("database.sqlite3", b"invalid-database", "SQLite"),
    ]
    for index, (member_name, replacement, expected) in enumerate(scenarios):
        members = dict(original)
        members[member_name] = replacement
        manifest = json.loads(members["manifest.json"])
        digest_name = "master_key_sha256" if member_name == "master.key" else "database_sha256"
        manifest[digest_name] = hashlib.sha256(replacement).hexdigest()
        members["manifest.json"] = json.dumps(manifest).encode("utf-8")
        invalid = tmp_path / f"invalid-{index}.zip"
        write_archive(invalid, members)

        with pytest.raises(BackupValidationError, match=expected):
            restore_backup(
                database_url=database_url(tmp_path / f"target-{index}.sqlite"),
                data_dir=tmp_path / f"target-data-{index}",
                input_path=invalid,
            )

    mismatched = dict(original)
    mismatched["database.sqlite3"] += b"tampered"
    mismatch_path = tmp_path / "mismatch.zip"
    write_archive(mismatch_path, mismatched)
    with pytest.raises(BackupValidationError, match="digest"):
        restore_backup(
            database_url=database_url(tmp_path / "mismatch-target.sqlite"),
            data_dir=tmp_path / "mismatch-data",
            input_path=mismatch_path,
        )


def test_restore_rejects_database_and_master_key_that_do_not_match(tmp_path: Path) -> None:
    archive_path, archived_key = make_backup(tmp_path / "backup")
    members = archive_members(archive_path)
    database_member = tmp_path / "database-with-secret.sqlite"
    database_member.write_bytes(members["database.sqlite3"])
    archived_key_path = tmp_path / "archived-master.key"
    archived_key_path.write_bytes(archived_key)
    secret_box = SecretBox(MasterKeyStore(archived_key_path).load())
    with closing(sqlite3.connect(database_member)) as connection:
        connection.execute(
            "CREATE TABLE account_secrets ("
            "account_id INTEGER PRIMARY KEY, username_encrypted TEXT NOT NULL, "
            "password_encrypted TEXT, cookies_encrypted TEXT)"
        )
        connection.execute(
            "INSERT INTO account_secrets "
            "(account_id, username_encrypted, password_encrypted, cookies_encrypted) "
            "VALUES (?, ?, NULL, NULL)",
            (1, secret_box.encrypt("student", purpose="account:1:username")),
        )
        connection.commit()

    members["database.sqlite3"] = database_member.read_bytes()
    members["master.key"] = create_key(tmp_path / "unrelated-key")
    manifest = json.loads(members["manifest.json"])
    manifest["database_sha256"] = hashlib.sha256(members["database.sqlite3"]).hexdigest()
    manifest["master_key_sha256"] = hashlib.sha256(members["master.key"]).hexdigest()
    members["manifest.json"] = json.dumps(manifest).encode("utf-8")
    mismatched = tmp_path / "mismatched-key.zip"
    write_archive(mismatched, members)

    with pytest.raises(BackupValidationError, match="do not match"):
        restore_backup(
            database_url=database_url(tmp_path / "target.sqlite"),
            data_dir=tmp_path / "target-data",
            input_path=mismatched,
        )

    assert not (tmp_path / "target.sqlite").exists()
    assert not (tmp_path / "target-data" / "master.key").exists()


def test_forced_restore_falls_back_to_durable_copy_for_cross_volume_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, archived_key = make_backup(tmp_path / "backup", value="restored")
    target_data = tmp_path / "target-data"
    target_database = tmp_path / "live" / "live.sqlite"
    create_database(target_database, "original")
    original_key = create_key(target_data)
    real_replace = os.replace

    def simulate_cross_volume(source: Path, destination: Path) -> None:
        if source == target_database:
            raise OSError(errno.EXDEV, "simulated cross-device move")
        real_replace(source, destination)

    monkeypatch.setattr(backup_module, "_replace", simulate_cross_volume)

    result = restore_backup(
        database_url=database_url(target_database),
        data_dir=target_data,
        input_path=archive_path,
        force=True,
        now=lambda: FIXED_TIME,
    )

    assert read_database(target_database) == "restored"
    assert (target_data / "master.key").read_bytes() == archived_key
    rollback = result.rollback_directory
    assert rollback is not None
    assert read_database(rollback / target_database.name) == "original"
    assert (rollback / "master.key").read_bytes() == original_key


def test_failed_restore_uses_cross_volume_fallback_when_returning_originals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, _archived_key = make_backup(tmp_path / "backup", value="restored")
    target_data = tmp_path / "target-data"
    target_database = tmp_path / "live.sqlite"
    create_database(target_database, "original")
    original_key = create_key(target_data)
    real_replace = os.replace
    rollback_directory = target_data.resolve() / "rollback-20260811T010203456789Z"

    def fail_install_and_simulate_cross_volume_return(
        source: Path,
        destination: Path,
    ) -> None:
        if source.name.startswith(".restore-master-key-") and destination.name == "master.key":
            raise OSError("simulated install failure")
        if source.parent == rollback_directory and destination == target_database:
            raise OSError(errno.EXDEV, "simulated cross-device return")
        real_replace(source, destination)

    monkeypatch.setattr(
        backup_module,
        "_replace",
        fail_install_and_simulate_cross_volume_return,
    )

    with pytest.raises(RestoreOperationError, match="original data was returned"):
        restore_backup(
            database_url=database_url(target_database),
            data_dir=target_data,
            input_path=archive_path,
            force=True,
            now=lambda: FIXED_TIME,
        )

    assert read_database(target_database) == "original"
    assert (target_data / "master.key").read_bytes() == original_key
    assert not (target_data / ".restore.lock").exists()


def test_interrupted_restore_keeps_recovery_journal_when_rollback_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, _archived_key = make_backup(tmp_path / "backup", value="restored")
    target_data = tmp_path / "target-data"
    target_database = tmp_path / "live.sqlite"
    create_database(target_database, "original")
    create_key(target_data)
    real_replace = os.replace
    real_durable_move = backup_module._durable_move

    def interrupt_install(source: Path, destination: Path) -> None:
        if source.name.startswith(".restore-master-key-") and destination.name == "master.key":
            raise KeyboardInterrupt
        real_replace(source, destination)

    def fail_returning_database(source: Path, destination: Path) -> None:
        if source.parent.name.startswith("rollback-") and destination == target_database:
            raise OSError("simulated rollback failure")
        real_durable_move(source, destination)

    monkeypatch.setattr(backup_module, "_replace", interrupt_install)
    monkeypatch.setattr(backup_module, "_durable_move", fail_returning_database)

    with pytest.raises(RestoreRollbackError, match="interrupted"):
        restore_backup(
            database_url=database_url(target_database),
            data_dir=target_data,
            input_path=archive_path,
            force=True,
            now=lambda: FIXED_TIME,
        )

    journal_path = target_data / ".restore.lock"
    assert journal_path.exists()
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["format"] == "chaoxing-app-restore-journal"
    assert journal["version"] == 1
    assert journal["phase"] == "rollback-incomplete"
    assert journal["database_path"] == str(target_database.resolve())
    assert journal["rollback_directory"] == str(
        target_data.resolve() / "rollback-20260811T010203456789Z"
    )
    assert {item["role"] for item in journal["moved_originals"]} == {
        "database",
        "master-key",
    }
