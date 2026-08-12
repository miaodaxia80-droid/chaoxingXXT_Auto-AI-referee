import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from chaoxing_app.infrastructure.backup import (
    BackupError,
    create_backup,
    restore_backup,
)
from chaoxing_app.infrastructure.legacy_import import (
    LegacyImportError,
    LegacyImportReport,
    import_legacy_database,
)
from chaoxing_app.settings import get_settings

_SENSITIVE_WARNING = (
    "Backup archives are unencrypted and contain the database and master key; "
    "treat them as highly sensitive."
)
_OFFLINE_WARNING = (
    "Restore requires the Chaoxing service and all workers to be completely stopped/offline."
)
_IMPORT_WARNING = (
    "Legacy import reads plaintext credentials from the source database; keep both "
    "applications offline and protect all database files."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chaoxing-app",
        description="Run the service or perform offline data maintenance.",
        epilog=f"SECURITY: {_SENSITIVE_WARNING} {_OFFLINE_WARNING}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="start the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5002)
    serve.add_argument("--reload", action="store_true")

    backup = subparsers.add_parser(
        "backup",
        help="create an unencrypted, highly sensitive backup archive",
        description=(
            "Create a consistent SQLite snapshot containing the database and master key. "
            f"{_SENSITIVE_WARNING}"
        ),
    )
    backup.add_argument("--output", type=Path, required=True, metavar="PATH")
    backup.add_argument(
        "--force",
        action="store_true",
        help="replace an existing backup file",
    )

    restore = subparsers.add_parser(
        "restore",
        help="restore a sensitive archive while the service is fully offline",
        description=f"{_OFFLINE_WARNING} {_SENSITIVE_WARNING}",
    )
    restore.add_argument("--input", type=Path, required=True, metavar="PATH")
    restore.add_argument(
        "--force",
        action="store_true",
        help="move existing live data into a timestamped rollback directory before restore",
    )

    legacy_import = subparsers.add_parser(
        "import-legacy",
        help="preview or apply a one-time import from the legacy Flask SQLite database",
        description=(
            "Read a legacy Flask SQLite database in read-only mode and import supported "
            "accounts and scheduler settings. The default is a dry-run. "
            f"{_IMPORT_WARNING}"
        ),
    )
    legacy_import.add_argument(
        "--source",
        type=Path,
        required=True,
        metavar="PATH",
        help="legacy Flask SQLite database (opened read-only)",
    )
    legacy_import.add_argument(
        "--apply",
        action="store_true",
        help="write the planned import to the configured target database",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        uvicorn.run(
            "chaoxing_app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            proxy_headers=False,
        )
        return 0

    if args.command == "import-legacy":
        print(f"OFFLINE REQUIRED: {_IMPORT_WARNING}", file=sys.stderr)
        settings = get_settings()
        try:
            report = import_legacy_database(
                source_path=args.source,
                database_url=settings.database_url,
                data_dir=settings.data_dir,
                apply=args.apply,
            )
        except LegacyImportError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        _print_legacy_report(report)
        return 0

    if args.command == "backup":
        print(f"SECURITY WARNING: {_SENSITIVE_WARNING}", file=sys.stderr)
    else:
        print(f"OFFLINE REQUIRED: {_OFFLINE_WARNING}", file=sys.stderr)
        print(f"SECURITY WARNING: {_SENSITIVE_WARNING}", file=sys.stderr)

    settings = get_settings()
    try:
        if args.command == "backup":
            backup_result = create_backup(
                database_url=settings.database_url,
                data_dir=settings.data_dir,
                output_path=args.output,
                force=args.force,
            )
            print(f"Sensitive backup written to: {backup_result.archive_path}")
            return 0

        restore_result = restore_backup(
            database_url=settings.database_url,
            data_dir=settings.data_dir,
            input_path=args.input,
            force=args.force,
        )
        print(f"Restore completed for: {restore_result.database_path}")
        if restore_result.rollback_directory is not None:
            print(f"Previous live data retained at: {restore_result.rollback_directory}")
        print("Keep the service offline until this command has fully exited.")
        return 0
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _print_legacy_report(report: LegacyImportReport) -> None:
    mode = "APPLIED" if report.applied else "DRY-RUN"
    print(
        f"Legacy import {mode}: total={report.total_accounts}, planned={report.planned}, "
        f"created={report.created}, duplicate={report.duplicate}, invalid={report.invalid}, "
        f"settings_mapped={report.settings_mapped}"
    )
    for result in report.results:
        hint = result.username_hint or "<unavailable>"
        suffix = f" ({result.error_code})" if result.error_code else ""
        print(f"row {result.row_id}: {result.status} {hint}{suffix}")
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not report.applied:
        print("No target data was changed. Re-run with --apply to perform the import.")


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
