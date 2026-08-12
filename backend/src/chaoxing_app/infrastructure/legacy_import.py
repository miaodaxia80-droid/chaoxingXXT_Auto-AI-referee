from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidTag
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from chaoxing_app.domain.settings import RunWindowSettings
from chaoxing_app.infrastructure.backup import (
    UnsupportedDatabaseError,
    sqlite_database_path,
)
from chaoxing_app.infrastructure.db.accounts import (
    AccountRepository,
    AccountUpdate,
    DuplicateAccountError,
    NewAccount,
    mask_username,
)
from chaoxing_app.infrastructure.db.engine import (
    create_database_engine,
    make_session_factory,
)
from chaoxing_app.infrastructure.db.system_settings import (
    SystemSettingsRepository,
    SystemSettingsUpdate,
)
from chaoxing_app.infrastructure.security.secrets import (
    MasterKeyStore,
    SecretBox,
    identity_fingerprint,
)

_MAX_SOURCE_BYTES = 128 * 1024 * 1024
_MAX_ACCOUNTS = 5_000
_MAX_SETTINGS = 500
_MAX_SETTING_KEY_LENGTH = 128
_MAX_SETTING_VALUE_LENGTH = 65_536

_SOURCE_USER_COLUMNS = {
    "id",
    "username",
    "password",
    "use_cookies",
    "cookies_data",
    "speed",
    "jobs",
    "notopen_action",
    "tiku_config",
    "notification_config",
    "enabled",
    "remark",
    "user_agent",
}
_SOURCE_SETTINGS_COLUMNS = {"key", "value"}
_TARGET_COLUMNS = {
    "accounts": {
        "id",
        "username_fingerprint",
        "enabled",
        "speed",
        "chapter_concurrency",
        "unopened_policy",
    },
    "account_secrets": {
        "account_id",
        "username_encrypted",
        "password_encrypted",
        "cookies_encrypted",
    },
    "system_settings": {
        "id",
        "worker_enabled",
        "run_window_enabled",
        "run_window_start",
        "run_window_end",
        "timezone",
    },
}


class LegacyImportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LegacyAccount:
    username: str
    password: str | None
    cookies: str | None
    remark: str
    user_agent: str
    speed: float
    chapter_concurrency: int
    unopened_policy: Literal["retry", "skip"]
    enabled: bool
    used_interactive_policy: bool = False

    @property
    def username_hint(self) -> str:
        return mask_username(self.username)


@dataclass(frozen=True, slots=True)
class LegacyAccountResult:
    row_id: int
    status: Literal["planned", "created", "duplicate", "invalid"]
    username_hint: str | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyImportReport:
    applied: bool
    total_accounts: int
    planned: int
    created: int
    duplicate: int
    invalid: int
    settings_mapped: int
    warnings: tuple[str, ...]
    results: tuple[LegacyAccountResult, ...]


@dataclass(frozen=True, slots=True)
class _ParsedSource:
    accounts: tuple[tuple[int, LegacyAccount], ...]
    invalid_results: tuple[LegacyAccountResult, ...]
    settings: SystemSettingsUpdate
    settings_mapped: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TargetInspection:
    accounts: tuple[tuple[int, str, str], ...]
    integration_secrets: tuple[tuple[str, str], ...]


def import_legacy_database(
    *,
    source_path: Path,
    database_url: str,
    data_dir: Path,
    apply: bool = False,
) -> LegacyImportReport:
    """Import supported legacy Flask data without ever mutating the source database."""

    source = source_path.expanduser().resolve(strict=False)
    if not source.is_file():
        raise LegacyImportError("legacy source must be an existing regular file")
    try:
        source_size = source.stat().st_size
    except OSError as exc:
        raise LegacyImportError("legacy source could not be inspected") from exc
    if source_size <= 0 or source_size > _MAX_SOURCE_BYTES:
        raise LegacyImportError("legacy source size is outside the supported limit")

    try:
        target = sqlite_database_path(database_url)
    except UnsupportedDatabaseError as exc:
        raise LegacyImportError(str(exc)) from exc
    if source == target:
        raise LegacyImportError("legacy source and target database must be different files")
    if not target.is_file():
        raise LegacyImportError(
            "target database does not exist; run Alembic migrations before importing"
        )

    parsed = _read_source(source)
    target_inspection = _inspect_target(target)
    target_accounts = target_inspection.accounts
    target_integration_secrets = target_inspection.integration_secrets
    target_account_count = len(target_accounts)
    key_store = MasterKeyStore((data_dir / "master.key").resolve(strict=False))
    master_key: bytes | None
    if key_store.path.is_file():
        try:
            master_key = key_store.load()
        except (OSError, ValueError) as exc:
            raise LegacyImportError("target master key failed validation") from exc
    elif target_account_count or target_integration_secrets:
        raise LegacyImportError(
            "target database has encrypted secrets but its master key is missing"
        )
    elif apply:
        try:
            master_key = key_store.load_or_create()
        except OSError as exc:
            raise LegacyImportError("target master key could not be created") from exc
    else:
        master_key = None

    existing_fingerprints: set[str] = set()
    if master_key is not None:
        existing_fingerprints = _validate_target_credentials(target_accounts, master_key)
        _validate_target_integration_secrets(target_integration_secrets, master_key)

    results = list(parsed.invalid_results)
    candidates: list[tuple[int, LegacyAccount]] = []
    seen_source_identities: set[str] = set()
    for row_id, account in parsed.accounts:
        normalized_identity = account.username.strip().casefold()
        fingerprint = (
            identity_fingerprint(account.username, key=master_key)
            if master_key is not None
            else None
        )
        if (
            normalized_identity in seen_source_identities
            or (fingerprint is not None and fingerprint in existing_fingerprints)
        ):
            results.append(
                LegacyAccountResult(
                    row_id=row_id,
                    status="duplicate",
                    username_hint=account.username_hint,
                    error_code="account_exists",
                )
            )
            continue
        seen_source_identities.add(normalized_identity)
        candidates.append((row_id, account))

    planned_settings = parsed.settings_mapped if target_account_count == 0 else 0
    warnings = list(parsed.warnings)
    if parsed.settings_mapped and target_account_count:
        warnings.append(
            "legacy global settings were not imported because the target already has accounts"
        )

    if not apply:
        results.extend(
            LegacyAccountResult(
                row_id=row_id,
                status="planned",
                username_hint=account.username_hint,
            )
            for row_id, account in candidates
        )
        return _build_report(
            parsed=parsed,
            applied=False,
            results=results,
            settings_mapped=planned_settings,
            warnings=tuple(warnings),
        )

    if master_key is None:  # pragma: no cover - guarded by the apply branch above
        raise LegacyImportError("target master key is unavailable")
    engine = create_database_engine(database_url)
    repository = AccountRepository(
        secret_box=SecretBox(master_key),
        fingerprint_key=master_key,
    )
    try:
        factory = make_session_factory(engine)
        with factory.begin() as session:
            for row_id, account in candidates:
                results.append(_create_account(session, repository, row_id, account))
            if planned_settings:
                SystemSettingsRepository().update(session, parsed.settings)
    except (OSError, ValueError, sqlite3.Error, SQLAlchemyError) as exc:
        raise LegacyImportError("legacy import failed; target transaction was rolled back") from exc
    finally:
        engine.dispose()
    return _build_report(
        parsed=parsed,
        applied=True,
        results=results,
        settings_mapped=planned_settings,
        warnings=tuple(warnings),
    )


def _create_account(
    session: Session,
    repository: AccountRepository,
    row_id: int,
    account: LegacyAccount,
) -> LegacyAccountResult:
    try:
        with session.begin_nested():
            created = repository.create(
                session,
                NewAccount(
                    username=account.username,
                    password=account.password,
                    cookies=account.cookies,
                    remark=account.remark,
                    user_agent=account.user_agent,
                    speed=account.speed,
                    chapter_concurrency=account.chapter_concurrency,
                    unopened_policy=account.unopened_policy,
                ),
            )
            if not account.enabled:
                repository.update(session, created.id, AccountUpdate(enabled=False))
    except DuplicateAccountError:
        return LegacyAccountResult(
            row_id=row_id,
            status="duplicate",
            username_hint=account.username_hint,
            error_code="account_exists",
        )
    return LegacyAccountResult(
        row_id=row_id,
        status="created",
        username_hint=account.username_hint,
    )


def _build_report(
    *,
    parsed: _ParsedSource,
    applied: bool,
    results: list[LegacyAccountResult],
    settings_mapped: int,
    warnings: tuple[str, ...],
) -> LegacyImportReport:
    ordered = tuple(sorted(results, key=lambda item: item.row_id))
    return LegacyImportReport(
        applied=applied,
        total_accounts=len(ordered),
        planned=sum(item.status == "planned" for item in ordered),
        created=sum(item.status == "created" for item in ordered),
        duplicate=sum(item.status == "duplicate" for item in ordered),
        invalid=sum(item.status == "invalid" for item in ordered),
        settings_mapped=settings_mapped,
        warnings=warnings,
        results=ordered,
    )


def _read_source(path: Path) -> _ParsedSource:
    try:
        with closing(_read_only_connection(path)) as connection:
            _validate_integrity(connection, "legacy source")
            _require_columns(connection, "users", _SOURCE_USER_COLUMNS, "legacy source")
            _require_columns(
                connection,
                "settings",
                _SOURCE_SETTINGS_COLUMNS,
                "legacy source",
            )
            rows = connection.execute(
                "SELECT id, username, password, use_cookies, cookies_data, speed, jobs, "
                "notopen_action, tiku_config, notification_config, enabled, remark, user_agent "
                "FROM users ORDER BY id LIMIT ?",
                (_MAX_ACCOUNTS + 1,),
            ).fetchall()
            if len(rows) > _MAX_ACCOUNTS:
                raise LegacyImportError("legacy source has too many account rows")
            setting_rows = connection.execute(
                'SELECT "key", value FROM settings ORDER BY "key" LIMIT ?',
                (_MAX_SETTINGS + 1,),
            ).fetchall()
            if len(setting_rows) > _MAX_SETTINGS:
                raise LegacyImportError("legacy source has too many setting rows")
    except LegacyImportError:
        raise
    except sqlite3.Error as exc:
        raise LegacyImportError("legacy source is not a readable SQLite database") from exc

    accounts: list[tuple[int, LegacyAccount]] = []
    invalid: list[LegacyAccountResult] = []
    answer_override_count = 0
    notification_override_count = 0
    for position, row in enumerate(rows, start=1):
        row_id = _safe_row_id(row["id"], fallback=position)
        try:
            account = _parse_account(row)
        except ValueError:
            invalid.append(
                LegacyAccountResult(
                    row_id=row_id,
                    status="invalid",
                    username_hint=_safe_username_hint(row["username"]),
                    error_code="invalid_legacy_row",
                )
            )
            continue
        accounts.append((row_id, account))
        answer_override_count += _has_unsupported_config(row["tiku_config"])
        notification_override_count += _has_unsupported_config(row["notification_config"])

    settings, mapped_count, ignored_setting_count, setting_warnings = _parse_settings(setting_rows)
    warnings = list(setting_warnings)
    if answer_override_count:
        warnings.append(
            f"{answer_override_count} account answer configuration(s) were not imported"
        )
    if notification_override_count:
        warnings.append(
            f"{notification_override_count} account notification configuration(s) were not imported"
        )
    interactive_policy_count = sum(
        account.used_interactive_policy for _row_id, account in accounts
    )
    if interactive_policy_count:
        warnings.append(
            f"{interactive_policy_count} interactive unopened-chapter policy value(s) "
            "were mapped to retry"
        )
    if ignored_setting_count:
        warnings.append(f"{ignored_setting_count} unsupported legacy setting(s) were ignored")
    return _ParsedSource(
        accounts=tuple(accounts),
        invalid_results=tuple(invalid),
        settings=settings,
        settings_mapped=mapped_count,
        warnings=tuple(warnings),
    )


def _parse_account(row: sqlite3.Row) -> LegacyAccount:
    username = _bounded_text(row["username"], maximum=256, required=True).strip()
    if not username:
        raise ValueError("username is blank")
    password = _bounded_text(row["password"], maximum=512, required=False) or None
    legacy_cookies = (
        _bounded_text(row["cookies_data"], maximum=32_768, required=False).strip() or None
    )
    cookies = legacy_cookies if _parse_bool(row["use_cookies"]) else None
    if password is None and cookies is None:
        raise ValueError("account has no usable credential")
    speed = _parse_float(row["speed"])
    if not 1.0 <= speed <= 2.0:
        raise ValueError("speed is outside the supported range")
    concurrency = _parse_int(row["jobs"])
    if not 1 <= concurrency <= 8:
        raise ValueError("chapter concurrency is outside the supported range")
    legacy_policy = _bounded_text(
        row["notopen_action"], maximum=32, required=True
    ).strip().casefold()
    policy_map: dict[str, Literal["retry", "skip"]] = {
        "retry": "retry",
        "continue": "skip",
    }
    used_interactive_policy = legacy_policy == "ask"
    if legacy_policy not in policy_map:
        # The old "ask" path blocks on an interactive terminal prompt, which the
        # unattended worker architecture cannot reproduce. Preserve the account and
        # choose the conservative retry behavior instead of silently dropping it.
        if legacy_policy == "ask":
            legacy_policy = "retry"
        else:
            raise ValueError("unopened policy is unsupported")
    return LegacyAccount(
        username=username,
        password=password,
        cookies=cookies,
        remark=_bounded_text(row["remark"], maximum=120, required=False).strip(),
        user_agent=_bounded_text(row["user_agent"], maximum=2_048, required=False).strip(),
        speed=speed,
        chapter_concurrency=concurrency,
        unopened_policy=policy_map[legacy_policy],
        enabled=_parse_bool(row["enabled"]),
        used_interactive_policy=used_interactive_policy,
    )


def _parse_settings(
    rows: list[sqlite3.Row],
) -> tuple[SystemSettingsUpdate, int, int, tuple[str, ...]]:
    values: dict[str, object] = {}
    ignored = 0
    warnings: list[str] = []
    supported = {
        "scheduler_paused",
        "run_window_enabled",
        "run_window_start",
        "run_window_end",
        "timezone",
    }
    for row in rows:
        key = _bounded_text(
            row["key"], maximum=_MAX_SETTING_KEY_LENGTH, required=True
        ).strip()
        raw_value = _bounded_text(
            row["value"], maximum=_MAX_SETTING_VALUE_LENGTH, required=False
        )
        if key not in supported:
            ignored += 1
            continue
        try:
            values[key] = _decode_legacy_setting(raw_value)
        except ValueError:
            warnings.append("one supported legacy setting had an invalid value and was ignored")

    mapped: dict[str, object] = {}
    try:
        if "scheduler_paused" in values:
            mapped["worker_enabled"] = not _parse_bool(values["scheduler_paused"])
        if "run_window_enabled" in values:
            mapped["run_window_enabled"] = _parse_bool(values["run_window_enabled"])
        for key in ("run_window_start", "run_window_end", "timezone"):
            if key in values:
                mapped[key] = _bounded_text(values[key], maximum=64, required=True).strip()
        validated = SystemSettingsUpdate(**mapped)  # type: ignore[arg-type]
        # Repository validation happens during apply. Constructing the domain value here
        # makes dry-run reject the same invalid clock/timezone combinations.
        RunWindowSettings(
            worker_enabled=(
                validated.worker_enabled
                if validated.worker_enabled is not None
                else True
            ),
            run_window_enabled=(
                validated.run_window_enabled
                if validated.run_window_enabled is not None
                else False
            ),
            run_window_start=validated.run_window_start or "00:00",
            run_window_end=validated.run_window_end or "00:00",
            timezone=validated.timezone or "Asia/Shanghai",
        )
    except (TypeError, ValueError):
        warnings.append("legacy run-window settings were invalid and were not imported")
        validated = SystemSettingsUpdate()
        mapped = {}
    return validated, len(mapped), ignored, tuple(warnings)


def _decode_legacy_setting(value: str) -> object:
    import json

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _inspect_target(path: Path) -> _TargetInspection:
    try:
        with closing(_read_only_connection(path)) as connection:
            _validate_integrity(connection, "target database")
            for table, columns in _TARGET_COLUMNS.items():
                _require_columns(connection, table, columns, "target database")
            accounts = tuple(
                (int(row[0]), str(row[1]), str(row[2]))
                for row in connection.execute(
                    "SELECT a.id, a.username_fingerprint, s.username_encrypted "
                    "FROM accounts AS a JOIN account_secrets AS s ON s.account_id = a.id "
                    "ORDER BY a.id"
                ).fetchall()
            )
            account_count = int(connection.execute("SELECT count(*) FROM accounts").fetchone()[0])
            if len(accounts) != account_count:
                raise LegacyImportError("target database has incomplete account secrets")
            table_row = connection.execute(
                "SELECT type FROM sqlite_master WHERE name = ?",
                ("integration_settings",),
            ).fetchone()
            integration_secrets: tuple[tuple[str, str], ...] = ()
            if table_row is not None:
                if table_row[0] != "table":
                    raise LegacyImportError(
                        "target database has an invalid integration_settings object"
                    )
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        'PRAGMA table_info("integration_settings")'
                    ).fetchall()
                }
                if {"kind", "secret_config_encrypted"} - columns:
                    raise LegacyImportError(
                        "target database has an unsupported integration_settings schema"
                    )
                integration_secrets = tuple(
                    (str(row[0]), str(row[1]))
                    for row in connection.execute(
                        "SELECT kind, secret_config_encrypted FROM integration_settings "
                        "WHERE secret_config_encrypted IS NOT NULL"
                    ).fetchall()
                )
    except LegacyImportError:
        raise
    except sqlite3.Error as exc:
        raise LegacyImportError("target database could not be inspected") from exc
    return _TargetInspection(
        accounts=accounts,
        integration_secrets=integration_secrets,
    )


def _validate_target_credentials(
    accounts: tuple[tuple[int, str, str], ...],
    master_key: bytes,
) -> set[str]:
    secret_box = SecretBox(master_key)
    fingerprints: set[str] = set()
    try:
        for account_id, fingerprint, encrypted_username in accounts:
            username = secret_box.decrypt(
                encrypted_username,
                purpose=f"account:{account_id}:username",
            )
            if identity_fingerprint(username, key=master_key) != fingerprint:
                raise LegacyImportError("target account identity failed validation")
            fingerprints.add(fingerprint)
    except (InvalidTag, UnicodeDecodeError, ValueError):
        raise LegacyImportError("target database secrets do not match its master key") from None
    return fingerprints


def _validate_target_integration_secrets(
    secrets: tuple[tuple[str, str], ...],
    master_key: bytes,
) -> None:
    secret_box = SecretBox(master_key)
    try:
        for kind, encrypted in secrets:
            secret_box.decrypt(encrypted, purpose=f"integration:{kind}:secrets")
    except (InvalidTag, UnicodeDecodeError, ValueError):
        raise LegacyImportError("target database secrets do not match its master key") from None


def _read_only_connection(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _validate_integrity(connection: sqlite3.Connection, label: str) -> None:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        raise LegacyImportError(f"{label} failed SQLite integrity validation")


def _require_columns(
    connection: sqlite3.Connection,
    table: str,
    expected: set[str],
    label: str,
) -> None:
    table_row = connection.execute(
        "SELECT type FROM sqlite_master WHERE name = ?",
        (table,),
    ).fetchone()
    if table_row is None or table_row["type"] != "table":
        raise LegacyImportError(f"{label} is missing the required {table} table")
    actual = {
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    if not expected <= actual:
        raise LegacyImportError(f"{label} has an unsupported {table} schema")


def _bounded_text(value: object, *, maximum: int, required: bool) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ValueError("value must be text")
    if len(value) > maximum:
        raise ValueError("text value exceeds the supported limit")
    return value


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"0", "false"}:
            return False
        if normalized in {"1", "true"}:
            return True
    raise ValueError("value must be a boolean")


def _parse_float(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("value must be numeric")
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError("value must be finite")
    return parsed


def _parse_int(value: object) -> int:
    parsed = _parse_float(value)
    if not parsed.is_integer():
        raise ValueError("value must be an integer")
    return int(parsed)


def _safe_row_id(value: object, *, fallback: int) -> int:
    try:
        parsed = _parse_int(value)
    except ValueError:
        return fallback
    return parsed if parsed > 0 else fallback


def _safe_username_hint(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        return None
    return mask_username(value)


def _has_unsupported_config(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, str) or len(value) > _MAX_SETTING_VALUE_LENGTH:
        return 1
    return int(value.strip() not in {"", "{}", "null"})
