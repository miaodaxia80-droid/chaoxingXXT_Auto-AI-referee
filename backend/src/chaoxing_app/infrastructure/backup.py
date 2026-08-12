from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Final, Protocol, TypeGuard

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from chaoxing_app.infrastructure.security.secrets import MasterKeyStore, SecretBox

_ARCHIVE_FORMAT: Final = "chaoxing-app-backup"
_ARCHIVE_VERSION: Final = 1
_MANIFEST_MEMBER: Final = "manifest.json"
_DATABASE_MEMBER: Final = "database.sqlite3"
_MASTER_KEY_MEMBER: Final = "master.key"
_ARCHIVE_MEMBERS: Final = frozenset({_MANIFEST_MEMBER, _DATABASE_MEMBER, _MASTER_KEY_MEMBER})
_MANIFEST_FIELDS: Final = frozenset(
    {
        "format",
        "version",
        "created_at_utc",
        "database_sha256",
        "master_key_sha256",
    }
)
_MAX_MANIFEST_BYTES: Final = 64 * 1024
_MAX_MASTER_KEY_BYTES: Final = 1024
# Keep restore staging bounded even when an archive contains a highly
# compressible database member.  The limit is intentionally independent of
# the ZIP metadata; extraction also enforces it while streaming.
_MAX_DATABASE_BYTES: Final = 1024 * 1024 * 1024
_SHA256 = frozenset("0123456789abcdef")
_RESTORE_LOCK_NAME: Final = ".restore.lock"
_RESTORE_JOURNAL_FORMAT: Final = "chaoxing-app-restore-journal"
_RESTORE_JOURNAL_VERSION: Final = 1


class _Digest(Protocol):
    def update(self, data: bytes) -> None: ...


class BackupError(Exception):
    """Base class for backup and restore failures."""


class UnsupportedDatabaseError(BackupError):
    """Raised when the configured database is not a file-backed SQLite database."""


class BackupValidationError(BackupError):
    """Raised when source data or an archive fails validation."""


class BackupDestinationExistsError(BackupError):
    """Raised when a backup would overwrite an existing path without --force."""


class RestoreTargetExistsError(BackupError):
    """Raised when restore would overwrite live data without --force."""


class RestoreOperationError(BackupError):
    """Raised when restore cannot complete after archive validation."""


class RestoreRollbackError(RestoreOperationError):
    """Raised when automatic recovery after a failed restore is incomplete."""


@dataclass(frozen=True, slots=True)
class BackupManifest:
    created_at_utc: datetime
    database_sha256: str
    master_key_sha256: str

    def as_json_bytes(self) -> bytes:
        payload = {
            "format": _ARCHIVE_FORMAT,
            "version": _ARCHIVE_VERSION,
            "created_at_utc": self.created_at_utc.astimezone(UTC).isoformat(),
            "database_sha256": self.database_sha256,
            "master_key_sha256": self.master_key_sha256,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class BackupResult:
    archive_path: Path
    database_path: Path
    created_at_utc: datetime


@dataclass(frozen=True, slots=True)
class RestoreResult:
    archive_path: Path
    database_path: Path
    master_key_path: Path
    rollback_directory: Path | None


@dataclass(frozen=True, slots=True)
class _StagedRestore:
    database_path: Path
    master_key_path: Path
    manifest: BackupManifest


@dataclass(frozen=True, slots=True)
class _OriginalFile:
    role: str
    source: Path
    stored: Path


@dataclass(slots=True)
class _RestoreJournal:
    path: Path
    archive_path: Path
    database_path: Path
    master_key_path: Path
    started_at_utc: datetime
    preserve_on_exit: bool = False
    last_phase: str = "validating"
    last_rollback_directory: Path | None = None
    last_moved_originals: tuple[_OriginalFile, ...] = ()
    last_installed: tuple[tuple[str, Path], ...] = ()

    def payload(
        self,
        *,
        phase: str,
        rollback_directory: Path | None,
        moved_originals: tuple[_OriginalFile, ...],
        installed: list[tuple[str, Path]],
    ) -> dict[str, object]:
        return {
            "format": _RESTORE_JOURNAL_FORMAT,
            "version": _RESTORE_JOURNAL_VERSION,
            "pid": os.getpid(),
            "started_at_utc": self.started_at_utc.isoformat(),
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "phase": phase,
            "archive_path": str(self.archive_path),
            "database_path": str(self.database_path),
            "master_key_path": str(self.master_key_path),
            "rollback_directory": (
                str(rollback_directory) if rollback_directory is not None else None
            ),
            "moved_originals": [
                {
                    "role": original.role,
                    "original_path": str(original.source),
                    "stored_path": str(original.stored),
                }
                for original in moved_originals
            ],
            "installed": [{"role": role, "target_path": str(target)} for role, target in installed],
        }

    def record(
        self,
        *,
        phase: str,
        rollback_directory: Path | None,
        moved_originals: tuple[_OriginalFile, ...],
        installed: list[tuple[str, Path]],
    ) -> None:
        self.last_phase = phase
        self.last_rollback_directory = rollback_directory
        self.last_moved_originals = moved_originals
        self.last_installed = tuple(installed)
        _write_restore_journal(
            self.path,
            self.payload(
                phase=phase,
                rollback_directory=rollback_directory,
                moved_originals=moved_originals,
                installed=installed,
            ),
        )

    def preserve(
        self,
        *,
        rollback_directory: Path | None,
        moved_originals: tuple[_OriginalFile, ...],
        installed: list[tuple[str, Path]],
    ) -> None:
        self.preserve_on_exit = True
        with suppress(RestoreOperationError):
            self.record(
                phase="rollback-incomplete",
                rollback_directory=rollback_directory,
                moved_originals=moved_originals,
                installed=installed,
            )

    def preserve_last(self) -> None:
        self.preserve(
            rollback_directory=self.last_rollback_directory,
            moved_originals=self.last_moved_originals,
            installed=list(self.last_installed),
        )


def sqlite_database_path(database_url: str, *, base_directory: Path | None = None) -> Path:
    try:
        parsed = make_url(database_url)
    except (ArgumentError, TypeError, ValueError):
        raise UnsupportedDatabaseError("database URL is not valid") from None
    if parsed.get_backend_name() != "sqlite":
        raise UnsupportedDatabaseError("backup and restore support only SQLite")
    database = parsed.database
    if not database or database == ":memory:" or database.startswith("file:"):
        raise UnsupportedDatabaseError("SQLite database must be backed by a regular file")
    path = Path(database).expanduser()
    if not path.is_absolute():
        path = (base_directory or Path.cwd()) / path
    return path.resolve(strict=False)


def create_backup(
    *,
    database_url: str,
    data_dir: Path,
    output_path: Path,
    force: bool = False,
    now: Callable[[], datetime] | None = None,
) -> BackupResult:
    database_path = sqlite_database_path(database_url)
    master_key_path = (data_dir / "master.key").resolve(strict=False)
    output = output_path.expanduser().resolve(strict=False)
    if database_path == master_key_path:
        raise BackupValidationError("database and master key paths must be distinct")
    if output in _live_data_paths(database_path, master_key_path):
        raise BackupValidationError("backup output cannot replace live application data")
    _validate_source_file(database_path, "SQLite database")
    _validate_source_file(master_key_path, "master key")
    _load_master_key(master_key_path)
    _prepare_backup_destination(output, force=force)
    output.parent.mkdir(parents=True, exist_ok=True)

    clock = now or (lambda: datetime.now(UTC))
    created_at = _aware_utc(clock())
    temporary_archive: Path | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix=".chaoxing-backup-",
            dir=output.parent,
        ) as temporary_directory:
            staging_directory = Path(temporary_directory)
            database_snapshot = staging_directory / _DATABASE_MEMBER
            key_snapshot = staging_directory / _MASTER_KEY_MEMBER
            _snapshot_sqlite(database_path, database_snapshot)
            _check_sqlite_integrity(database_snapshot)
            shutil.copyfile(master_key_path, key_snapshot)
            snapshot_key = _load_master_key(key_snapshot)
            _validate_database_key_pair(database_snapshot, snapshot_key)

            manifest = BackupManifest(
                created_at_utc=created_at,
                database_sha256=_sha256_file(database_snapshot),
                master_key_sha256=_sha256_file(key_snapshot),
            )
            temporary_archive = _temporary_file_path(output.parent, ".backup-archive-")
            _write_archive(
                temporary_archive,
                manifest=manifest,
                database_path=database_snapshot,
                master_key_path=key_snapshot,
            )
            _fsync_file(temporary_archive)
            _prepare_backup_destination(output, force=force)
            _replace(temporary_archive, output)
            _fsync_directory(output.parent)
            temporary_archive = None
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile):
        raise BackupError("could not create the backup archive") from None
    finally:
        if temporary_archive is not None:
            with suppress(OSError):
                temporary_archive.unlink(missing_ok=True)

    return BackupResult(
        archive_path=output,
        database_path=database_path,
        created_at_utc=created_at,
    )


def restore_backup(
    *,
    database_url: str,
    data_dir: Path,
    input_path: Path,
    force: bool = False,
    now: Callable[[], datetime] | None = None,
) -> RestoreResult:
    database_path = sqlite_database_path(database_url)
    resolved_data_dir = data_dir.expanduser().resolve(strict=False)
    master_key_path = (resolved_data_dir / "master.key").resolve(strict=False)
    archive_path = input_path.expanduser().resolve(strict=False)
    if database_path == master_key_path:
        raise RestoreOperationError("database and master key paths must be distinct")
    if archive_path in _live_data_paths(database_path, master_key_path):
        raise RestoreOperationError("backup archive cannot also be a live data file")
    _validate_source_file(archive_path, "backup archive")

    live_paths = _live_data_paths(database_path, master_key_path)
    existing = tuple(path for path in live_paths if _path_exists(path))
    if existing and not force:
        raise RestoreTargetExistsError(
            "restore target contains existing data; use --force only while the service is offline"
        )

    database_path.parent.mkdir(parents=True, exist_ok=True)
    master_key_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_data_dir.mkdir(parents=True, exist_ok=True)

    with _restore_lock(
        resolved_data_dir,
        archive_path=archive_path,
        database_path=database_path,
        master_key_path=master_key_path,
    ) as journal:
        return _restore_backup_locked(
            archive_path=archive_path,
            database_path=database_path,
            master_key_path=master_key_path,
            data_dir=resolved_data_dir,
            live_paths=live_paths,
            force=force,
            now=now,
            journal=journal,
        )


def _restore_backup_locked(
    *,
    archive_path: Path,
    database_path: Path,
    master_key_path: Path,
    data_dir: Path,
    live_paths: tuple[Path, ...],
    force: bool,
    now: Callable[[], datetime] | None,
    journal: _RestoreJournal,
) -> RestoreResult:
    staged: _StagedRestore | None = None
    moved_originals: tuple[_OriginalFile, ...] = ()
    rollback_directory: Path | None = None
    installed: list[tuple[str, Path]] = []
    try:
        staged = _stage_and_validate_archive(
            archive_path,
            database_directory=database_path.parent,
            key_directory=master_key_path.parent,
        )
        journal.record(
            phase="validated",
            rollback_directory=None,
            moved_originals=(),
            installed=[],
        )
        existing = tuple(path for path in live_paths if _path_exists(path))
        if existing and not force:
            raise RestoreTargetExistsError(
                "restore target changed during validation; no data was overwritten"
            )
        if existing:
            rollback_directory = _create_rollback_directory(
                data_dir,
                _aware_utc((now or (lambda: datetime.now(UTC)))()),
            )
            journal.record(
                phase="moving-originals",
                rollback_directory=rollback_directory,
                moved_originals=(),
                installed=[],
            )
            moved_originals = _move_original_files(
                database_path=database_path,
                master_key_path=master_key_path,
                rollback_directory=rollback_directory,
                on_progress=lambda moved: journal.record(
                    phase="moving-originals",
                    rollback_directory=rollback_directory,
                    moved_originals=moved,
                    installed=[],
                ),
            )

        installed.append(("database", database_path))
        journal.record(
            phase="installing-database",
            rollback_directory=rollback_directory,
            moved_originals=moved_originals,
            installed=installed,
        )
        _replace(staged.database_path, database_path)
        installed.append(("master-key", master_key_path))
        journal.record(
            phase="installing-master-key",
            rollback_directory=rollback_directory,
            moved_originals=moved_originals,
            installed=installed,
        )
        _replace(staged.master_key_path, master_key_path)
        _fsync_directory(database_path.parent)
        if master_key_path.parent != database_path.parent:
            _fsync_directory(master_key_path.parent)
        journal.record(
            phase="completed",
            rollback_directory=rollback_directory,
            moved_originals=moved_originals,
            installed=installed,
        )
    except (KeyboardInterrupt, SystemExit):
        if installed or moved_originals:
            recovery_directory, recovery_failures = _recover_failed_restore(
                installed=installed,
                moved_originals=moved_originals,
                rollback_directory=rollback_directory,
                data_dir=data_dir,
            )
            if recovery_failures:
                journal.preserve(
                    rollback_directory=recovery_directory,
                    moved_originals=moved_originals,
                    installed=installed,
                )
                raise RestoreRollbackError(
                    "restore was interrupted and automatic rollback is incomplete; "
                    f"inspect {recovery_directory} before restarting the service"
                ) from None
        raise
    except (BackupError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        if not installed and not moved_originals:
            if isinstance(exc, RestoreRollbackError):
                journal.preserve_last()
            if isinstance(exc, BackupError):
                raise
            raise RestoreOperationError("restore failed before live data was changed") from None
        recovery_directory, recovery_failures = _recover_failed_restore(
            installed=installed,
            moved_originals=moved_originals,
            rollback_directory=rollback_directory,
            data_dir=data_dir,
        )
        if recovery_failures:
            journal.preserve(
                rollback_directory=recovery_directory,
                moved_originals=moved_originals,
                installed=installed,
            )
            raise RestoreRollbackError(
                "restore failed and automatic rollback is incomplete; "
                f"inspect {recovery_directory} before restarting the service"
            ) from None
        raise RestoreOperationError(
            "restore failed; original data was returned to its configured paths"
        ) from None
    finally:
        if staged is not None:
            with suppress(OSError):
                staged.database_path.unlink(missing_ok=True)
            with suppress(OSError):
                staged.master_key_path.unlink(missing_ok=True)

    return RestoreResult(
        archive_path=archive_path,
        database_path=database_path,
        master_key_path=master_key_path,
        rollback_directory=rollback_directory,
    )


@contextmanager
def _restore_lock(
    data_dir: Path,
    *,
    archive_path: Path,
    database_path: Path,
    master_key_path: Path,
) -> Iterator[_RestoreJournal]:
    lock_path = data_dir / _RESTORE_LOCK_NAME
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RestoreOperationError(
            "another restore is active or a previous restore was interrupted; "
            f"inspect and remove {lock_path} only while the service is offline"
        ) from None
    started_at = datetime.now(UTC)
    journal = _RestoreJournal(
        path=lock_path,
        archive_path=archive_path,
        database_path=database_path,
        master_key_path=master_key_path,
        started_at_utc=started_at,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
            json.dump(
                journal.payload(
                    phase="validating",
                    rollback_directory=None,
                    moved_originals=(),
                    installed=[],
                ),
                lock_file,
                sort_keys=True,
            )
            lock_file.flush()
            os.fsync(lock_file.fileno())
        _fsync_directory(data_dir)
        yield journal
    finally:
        if not journal.preserve_on_exit:
            with suppress(OSError):
                lock_path.unlink(missing_ok=True)
            _fsync_directory(data_dir)


def _validate_source_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise BackupValidationError(f"{label} does not exist or is not a regular file")


def _prepare_backup_destination(path: Path, *, force: bool) -> None:
    if _path_exists(path) and (not force or path.is_dir()):
        raise BackupDestinationExistsError(
            "backup output already exists; use --force to replace the existing file"
        )


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _load_master_key(path: Path) -> bytes:
    try:
        return MasterKeyStore(path).load()
    except (OSError, ValueError):
        raise BackupValidationError("master key failed validation") from None


def _sqlite_read_only_uri(path: Path) -> str:
    return f"{path.resolve(strict=False).as_uri()}?mode=ro"


def _snapshot_sqlite(source_path: Path, destination_path: Path) -> None:
    try:
        with (
            closing(sqlite3.connect(_sqlite_read_only_uri(source_path), uri=True)) as source,
            closing(sqlite3.connect(destination_path)) as destination,
        ):
            source.backup(destination)
            destination.commit()
    except sqlite3.Error:
        raise BackupValidationError("SQLite snapshot could not be created") from None


def _check_sqlite_integrity(path: Path) -> None:
    try:
        with closing(sqlite3.connect(_sqlite_read_only_uri(path), uri=True)) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error:
        raise BackupValidationError("SQLite database failed integrity validation") from None
    if rows != [("ok",)]:
        raise BackupValidationError("SQLite database failed integrity validation")


def _validate_database_key_pair(database_path: Path, master_key: bytes) -> None:
    secret_box = SecretBox(master_key)
    try:
        with closing(sqlite3.connect(_sqlite_read_only_uri(database_path), uri=True)) as connection:
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if "account_secrets" in tables:
                _validate_account_secret_rows(connection, secret_box)
            if "integration_settings" in tables:
                _validate_integration_secret_rows(connection, secret_box)
    except sqlite3.Error:
        raise BackupValidationError(
            "SQLite database secret metadata could not be inspected"
        ) from None
    except Exception:
        raise BackupValidationError(
            "database encrypted secrets do not match the master key"
        ) from None


def _validate_account_secret_rows(
    connection: sqlite3.Connection,
    secret_box: SecretBox,
) -> None:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(account_secrets)")}
    expected = {
        "account_id",
        "username_encrypted",
        "password_encrypted",
        "cookies_encrypted",
    }
    if not expected.issubset(columns):
        raise ValueError("account secret schema is incompatible")
    rows = connection.execute(
        "SELECT account_id, username_encrypted, password_encrypted, cookies_encrypted "
        "FROM account_secrets"
    )
    for account_id, username, password, cookies in rows:
        if not isinstance(account_id, int) or not isinstance(username, str):
            raise ValueError("account secret row is invalid")
        secret_box.decrypt(username, purpose=f"account:{account_id}:username")
        if password is not None:
            if not isinstance(password, str):
                raise ValueError("account password secret is invalid")
            secret_box.decrypt(password, purpose=f"account:{account_id}:password")
        if cookies is not None:
            if not isinstance(cookies, str):
                raise ValueError("account cookie secret is invalid")
            secret_box.decrypt(cookies, purpose=f"account:{account_id}:cookies")


def _validate_integration_secret_rows(
    connection: sqlite3.Connection,
    secret_box: SecretBox,
) -> None:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(integration_settings)")}
    expected = {"kind", "secret_config_encrypted"}
    if not expected.issubset(columns):
        raise ValueError("integration secret schema is incompatible")
    rows = connection.execute(
        "SELECT kind, secret_config_encrypted FROM integration_settings "
        "WHERE secret_config_encrypted IS NOT NULL"
    )
    for kind, encrypted in rows:
        if not isinstance(kind, str) or not isinstance(encrypted, str):
            raise ValueError("integration secret row is invalid")
        secret_box.decrypt(encrypted, purpose=f"integration:{kind}:secrets")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _temporary_file_path(directory: Path, prefix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=prefix, dir=directory)
    os.close(descriptor)
    return Path(raw_path)


def _write_archive(
    archive_path: Path,
    *,
    manifest: BackupManifest,
    database_path: Path,
    master_key_path: Path,
) -> None:
    try:
        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            archive.writestr(_MANIFEST_MEMBER, manifest.as_json_bytes())
            archive.write(database_path, _DATABASE_MEMBER)
            archive.write(master_key_path, _MASTER_KEY_MEMBER)
    except (OSError, ValueError, zipfile.BadZipFile):
        raise BackupError("could not write the backup archive") from None
    with suppress(OSError):
        os.chmod(archive_path, 0o600)


def _parse_manifest(raw: bytes) -> BackupManifest:
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise BackupValidationError("backup manifest is too large")
    try:
        payload: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BackupValidationError("backup manifest is not valid JSON") from None
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise BackupValidationError("backup manifest must be a JSON object")
    if frozenset(payload) != _MANIFEST_FIELDS:
        raise BackupValidationError("backup manifest contains unexpected fields")
    archive_format = payload.get("format")
    archive_version = payload.get("version")
    if (
        not isinstance(archive_format, str)
        or archive_format != _ARCHIVE_FORMAT
        or isinstance(archive_version, bool)
        or not isinstance(archive_version, int)
        or archive_version != _ARCHIVE_VERSION
    ):
        raise BackupValidationError("backup format or version is not supported")

    created_value = payload.get("created_at_utc")
    database_digest = payload.get("database_sha256")
    key_digest = payload.get("master_key_sha256")
    if not isinstance(created_value, str):
        raise BackupValidationError("backup manifest timestamp is invalid")
    try:
        created_at = datetime.fromisoformat(created_value)
    except ValueError:
        raise BackupValidationError("backup manifest timestamp is invalid") from None
    if created_at.tzinfo is None:
        raise BackupValidationError("backup manifest timestamp must include a timezone")
    if not _valid_sha256(database_digest) or not _valid_sha256(key_digest):
        raise BackupValidationError("backup manifest digest is invalid")
    return BackupManifest(
        created_at_utc=created_at.astimezone(UTC),
        database_sha256=database_digest,
        master_key_sha256=key_digest,
    )


def _valid_sha256(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256


def _validate_archive_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(names) != len(_ARCHIVE_MEMBERS) or frozenset(names) != _ARCHIVE_MEMBERS:
        raise BackupValidationError(
            "backup archive must contain only manifest.json, database.sqlite3, and master.key"
        )
    validated: dict[str, zipfile.ZipInfo] = {}
    for entry in entries:
        member_path = PurePosixPath(entry.filename)
        mode = entry.external_attr >> 16
        if (
            entry.is_dir()
            or member_path.is_absolute()
            or ".." in member_path.parts
            or "\\" in entry.filename
            or entry.flag_bits & 0x1
            or stat.S_ISLNK(mode)
        ):
            raise BackupValidationError("backup archive contains an unsafe member")
        validated[entry.filename] = entry
    if validated[_MANIFEST_MEMBER].file_size > _MAX_MANIFEST_BYTES:
        raise BackupValidationError("backup manifest is too large")
    if validated[_MASTER_KEY_MEMBER].file_size > _MAX_MASTER_KEY_BYTES:
        raise BackupValidationError("backup master key member is too large")
    if validated[_DATABASE_MEMBER].file_size > _MAX_DATABASE_BYTES:
        raise BackupValidationError("backup database member is too large")
    return validated


@contextmanager
def _open_validated_archive(
    archive_path: Path,
) -> Iterator[tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo], BackupManifest]]:
    try:
        archive = zipfile.ZipFile(archive_path, mode="r")
    except (OSError, zipfile.BadZipFile):
        raise BackupValidationError("backup archive is not a valid ZIP file") from None
    try:
        members = _validate_archive_members(archive)
        try:
            manifest_bytes = archive.read(members[_MANIFEST_MEMBER])
        except (OSError, RuntimeError, zipfile.BadZipFile):
            raise BackupValidationError("backup manifest could not be read") from None
        yield archive, members, _parse_manifest(manifest_bytes)
    finally:
        archive.close()


def _stage_and_validate_archive(
    archive_path: Path,
    *,
    database_directory: Path,
    key_directory: Path,
) -> _StagedRestore:
    database_stage = _temporary_file_path(database_directory, ".restore-database-")
    key_stage = _temporary_file_path(key_directory, ".restore-master-key-")
    try:
        with _open_validated_archive(archive_path) as (archive, members, manifest):
            database_digest = _extract_member(
                archive,
                members[_DATABASE_MEMBER],
                database_stage,
                max_bytes=_MAX_DATABASE_BYTES,
            )
            key_digest = _extract_member(
                archive,
                members[_MASTER_KEY_MEMBER],
                key_stage,
                max_bytes=_MAX_MASTER_KEY_BYTES,
            )
        if database_digest != manifest.database_sha256:
            raise BackupValidationError("backup database digest does not match the manifest")
        if key_digest != manifest.master_key_sha256:
            raise BackupValidationError("backup master key digest does not match the manifest")
        _check_sqlite_integrity(database_stage)
        master_key = _load_master_key(key_stage)
        _validate_database_key_pair(database_stage, master_key)
        try:
            os.chmod(database_stage, 0o600)
            os.chmod(key_stage, 0o600)
        except OSError:
            pass
        return _StagedRestore(database_stage, key_stage, manifest)
    except BaseException:
        with suppress(OSError):
            database_stage.unlink(missing_ok=True)
        with suppress(OSError):
            key_stage.unlink(missing_ok=True)
        raise


def _extract_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    destination: Path,
    *,
    max_bytes: int | None = None,
) -> str:
    digest = hashlib.sha256()
    try:
        with archive.open(member, mode="r") as source, destination.open("wb") as target:
            _copy_and_hash(source, target, digest, max_bytes=max_bytes)
            target.flush()
            os.fsync(target.fileno())
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
    ):
        raise BackupValidationError("backup archive member could not be extracted") from None
    return digest.hexdigest()


def _copy_and_hash(
    source: IO[bytes],
    target: IO[bytes],
    digest: _Digest,
    *,
    max_bytes: int | None = None,
) -> None:
    copied = 0
    while True:
        read_size = 1024 * 1024
        if max_bytes is not None:
            remaining = max_bytes - copied
            if remaining < 0:
                raise BackupValidationError("backup archive member is too large")
            # Read one byte beyond the limit so a stream whose ZIP metadata is
            # inaccurate is still rejected before that byte reaches disk.
            read_size = min(read_size, remaining + 1)
        block = source.read(read_size)
        if not block:
            return
        if max_bytes is not None and copied + len(block) > max_bytes:
            raise BackupValidationError("backup archive member is too large")
        target.write(block)
        digest.update(block)
        copied += len(block)


def _live_data_paths(database_path: Path, master_key_path: Path) -> tuple[Path, ...]:
    return (
        database_path,
        master_key_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
        Path(f"{database_path}-journal"),
    )


def _create_rollback_directory(data_dir: Path, timestamp: datetime) -> Path:
    stem = f"rollback-{timestamp.astimezone(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
    for suffix in range(1000):
        name = stem if suffix == 0 else f"{stem}-{suffix}"
        candidate = data_dir / name
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return candidate
    raise RestoreOperationError("could not allocate a rollback directory")


def _move_original_files(
    *,
    database_path: Path,
    master_key_path: Path,
    rollback_directory: Path,
    on_progress: Callable[[tuple[_OriginalFile, ...]], None] | None = None,
) -> tuple[_OriginalFile, ...]:
    candidates = (
        ("database", database_path),
        ("master-key", master_key_path),
        ("database-wal", Path(f"{database_path}-wal")),
        ("database-shm", Path(f"{database_path}-shm")),
        ("database-journal", Path(f"{database_path}-journal")),
    )
    existing = tuple((role, path) for role, path in candidates if _path_exists(path))
    destinations: set[Path] = set()
    originals: list[_OriginalFile] = []
    for role, source in existing:
        destination = rollback_directory / source.name
        if destination.name == "rollback-manifest.json" or destination in destinations:
            destination = rollback_directory / f"{role}-{source.name}"
        destinations.add(destination)
        originals.append(_OriginalFile(role, source, destination))

    _write_rollback_manifest(rollback_directory, originals)
    moved: list[_OriginalFile] = []
    try:
        for original in originals:
            if original.source.is_dir():
                raise RestoreOperationError("restore target contains a directory")
            if on_progress is not None:
                # Record intent before the destructive half of the move so a
                # hard process exit still leaves both paths in the journal.
                on_progress((*moved, original))
            _durable_move(original.source, original.stored)
            moved.append(original)
            if on_progress is not None:
                on_progress(tuple(moved))
    except BaseException as exc:
        failures = _return_original_files(tuple(moved))
        if failures:
            raise RestoreRollbackError(
                "could not move live data into rollback storage and recovery is incomplete; "
                f"inspect {rollback_directory}"
            ) from None
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise RestoreOperationError(
            "could not move live data into rollback storage; original data was restored"
        ) from None
    return tuple(moved)


def _write_rollback_manifest(
    rollback_directory: Path,
    originals: list[_OriginalFile],
) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "reason": "forced offline restore",
        "originals": [
            {
                "role": original.role,
                "original_path": str(original.source),
                "stored_as": original.stored.name,
            }
            for original in originals
        ],
    }
    manifest_path = rollback_directory / "rollback-manifest.json"
    try:
        with manifest_path.open("x", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True, indent=2)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        raise RestoreOperationError("could not write rollback metadata") from None


def _return_original_files(originals: tuple[_OriginalFile, ...]) -> int:
    failures = 0
    for original in reversed(originals):
        if _path_exists(original.source):
            failures += 1
            continue
        try:
            _durable_move(original.stored, original.source)
        except OSError:
            failures += 1
    return failures


def _recover_failed_restore(
    *,
    installed: list[tuple[str, Path]],
    moved_originals: tuple[_OriginalFile, ...],
    rollback_directory: Path | None,
    data_dir: Path,
) -> tuple[Path, int]:
    recovery_directory = rollback_directory
    if recovery_directory is None:
        recovery_directory = _create_rollback_directory(data_dir, datetime.now(UTC))
    failures = 0
    for role, target in reversed(installed):
        if not _path_exists(target):
            continue
        try:
            quarantine = _unused_path(recovery_directory / f"failed-restored-{role}")
        except RestoreRollbackError:
            failures += 1
            continue
        try:
            _durable_move(target, quarantine)
        except OSError:
            failures += 1
    failures += _return_original_files(moved_originals)
    return recovery_directory, failures


def _unused_path(candidate: Path) -> Path:
    if not _path_exists(candidate):
        return candidate
    for suffix in range(1, 1000):
        alternative = candidate.with_name(f"{candidate.name}-{suffix}")
        if not _path_exists(alternative):
            return alternative
    raise RestoreRollbackError("could not allocate recovery storage for failed restore data")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise BackupValidationError("backup timestamp must include a timezone")
    return value.astimezone(UTC)


def _replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _durable_move(source: Path, destination: Path) -> None:
    try:
        _replace(source, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    else:
        # The rename already changed state. Some NAS filesystems do not allow
        # directory fsync; treating that as an unmoved file would make rollback
        # bookkeeping incorrect, so synchronization is best effort here.
        with suppress(OSError):
            _fsync_directory(source.parent)
        if destination.parent != source.parent:
            with suppress(OSError):
                _fsync_directory(destination.parent)
        return

    temporary = _temporary_file_path(destination.parent, ".restore-move-")
    try:
        shutil.copyfile(source, temporary)
        with suppress(OSError):
            shutil.copymode(source, temporary)
        _fsync_data_file(temporary)
        _replace(temporary, destination)
        _fsync_directory(destination.parent)
        source.unlink()
        with suppress(OSError):
            _fsync_directory(source.parent)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _write_restore_journal(path: Path, payload: dict[str, object]) -> None:
    temporary = _temporary_file_path(path.parent, ".restore-journal-")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True, indent=2)
            output.flush()
            os.fsync(output.fileno())
        with suppress(OSError):
            os.chmod(temporary, 0o600)
        _replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError:
        raise RestoreOperationError("could not persist restore recovery metadata") from None
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _fsync_data_file(path: Path) -> None:
    with path.open("rb+") as source:
        os.fsync(source.fileno())


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb+") as source:
            os.fsync(source.fileno())
    except OSError:
        raise BackupError("could not flush the backup archive to disk") from None


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
