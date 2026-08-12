# Chaoxing Rewrite Architecture

This directory defines the contract for the replacement application. The
legacy Flask application remains only as a behavioral reference, sanitized
fixture source, and offline import source; it is never a runtime dependency of
the replacement.

## Goals

- Keep the proven Chaoxing protocol knowledge while replacing the current
  control plane, scheduler, persistence, and UI.
- Make every task transition persistent, explicit, and idempotent.
- Keep deployment practical on Windows, Docker, NAS devices, and 64-bit ARM.
- Never expose account passwords, cookies, provider tokens, or API keys from a
  read endpoint.

## Runtime Shape

The production application is a single deployable unit with three internal
parts:

1. FastAPI serves the versioned API, the built Vue application, and SSE event
   streams.
2. A scheduler claims persisted tasks with database leases and starts one
   isolated child process per active account.
3. A worker process owns the account runtime, emits structured events, and
   updates chapter results through repositories.

SQLite remains the default database. WAL mode, foreign keys, a busy timeout,
short write transactions, account leases, and fencing tokens are mandatory.
The design must remain correct if a second server instance is started by
mistake, even though the supported deployment uses one Uvicorn worker.

### Database schema management

Alembic owns development and production schema upgrades. From `backend/`, an
empty database can be initialized with:

```shell
python -m alembic upgrade head
```

The migration environment uses `CX_DATABASE_URL` when it is set. A one-off URL
can instead be supplied with
`python -m alembic -x database_url=sqlite:///path/to/database.db upgrade head`.
Back up the database and master key before every upgrade.

The offline CLI backup command snapshots SQLite and packages it with the
matching `master.key`. Creation and restore validate every encrypted account
and integration secret that is present, so a database paired with an unrelated
key is rejected before live data is changed. Forced restore retains the old
database, key, and SQLite sidecars in a timestamped `data/rollback-*` directory.
Moves to that directory also support deployments where the configured database
and persistent data directory are on different volumes.

Restore writes `data/.restore.lock` as a durable recovery journal while it is
running. A successful restore, or a failure whose automatic rollback completed,
removes it. If rollback is incomplete, the journal is deliberately retained and
records the phase, configured target paths, rollback directory, moved originals,
and attempted installs. Keep the service offline and inspect both the journal
and referenced rollback directory before manually recovering files and removing
the lock; never delete an unexplained lock merely to make startup proceed.

Development and production startup are fail-closed: they inspect
`alembic_version` and refuse to start unless the database is exactly at the
application's current Alembic head. Run `upgrade head` before every non-test
start after pulling migrations. SQLAlchemy `create_all()` is restricted to
`CX_ENVIRONMENT=test`, where tests create isolated disposable databases. It is
not a development or production schema-management path. Do not `stamp` a
legacy, hand-created, or partially upgraded schema.

### Legacy data import

`chaoxing-app import-legacy --source PATH` is an offline, bounded importer for
the legacy Flask SQLite schema. It opens the source with SQLite read-only and
`query_only` modes, checks integrity and expected columns, and defaults to a
dry-run that neither changes the target nor creates a master key. `--apply` is
required to write the planned accounts and supported scheduler settings into
an already-migrated target database. Source and target must be different files.

Supported account fields are username, password, the explicitly enabled Cookie,
enabled state, remark, User-Agent, speed, chapter concurrency, and unopened
chapter policy. `use_cookies=false` always suppresses legacy `cookies_data`.
Imported secrets are encrypted with the target master key. Supported global
settings are scheduler pause state, run-window state/start/end, and timezone;
they are applied only when the target has no accounts. Existing or duplicate
accounts are never overwritten.

The importer intentionally does not migrate administrator state, tasks,
chapters, events, answer cache, account answer settings, notification settings,
or unknown legacy settings. Unsupported values are skipped or reported with
non-secret warning/error codes. Both applications must remain offline because
the source database can contain plaintext credentials.

## Source Boundaries

```text
backend/src/chaoxing_app/
|- domain/          Pure states and policies
|- application/     Use cases and ports
|- platform/        Chaoxing protocol and answer providers
|- infrastructure/  Database, encryption, scheduler, logging
|- api/             HTTP schemas, routes, auth, SSE
`- worker/          Process supervision and execution

frontend/src/
|- api/             Typed API client
|- components/      Shared UI primitives
|- domain/          Client-side display and state rules
|- layouts/         Application shell
|- pages/           Route-level views
|- router/          Routes and guards
`- stores/          Client-only state
```

The domain layer cannot import FastAPI, SQLAlchemy, requests, or UI code. The
platform layer cannot import the database or web API. API handlers cannot call
the legacy `Chaoxing` object directly.

## Task Rules

- A task is claimed once through a database transaction and an account lease.
- An account can have at most one valid lease.
- Successful chapters are never run again during recovery.
- Only all-successful or already-completed chapters produce `succeeded`.
- Unsubmitted, skipped, or failed chapters produce `needs_attention`.
- Login failures, missing courses, and fatal protocol failures produce
  `failed`.
- Pause and cancel are desired states checked at safe points. Forceful process
  termination is a timeout fallback, not the normal control path.
- The run window prevents new work and pauses active work at a safe point. It
  resumes the same task record instead of creating a duplicate task.
- Multi-course creation and bulk task commands isolate each item in a savepoint
  and report partial success. A failure in one item does not roll back unrelated
  successful items.
- Task deletion is limited to terminal states. Bulk deletion and history
  cleanup cannot delete active work; terminal deletion cascades to owned
  chapter and event rows.

The executable state-transition rules live in
`backend/src/chaoxing_app/domain/tasks.py` and are covered by unit tests.

## Security Baseline

- First-run setup creates a single administrator without a default password.
- Browser sessions use opaque, server-side tokens in HttpOnly SameSite
  cookies. Mutations validate CSRF/Origin.
- Account credentials and provider secrets are encrypted with AES-GCM. The
  master key lives outside the database in the persistent data directory.
- Read schemas return presence flags such as `has_password`, never secret
  values.
- HTTPS certificate validation is enabled for platform and provider requests.
- Structured events are redacted before persistence or SSE delivery.
- Custom provider endpoints reject unsafe schemes and private-network targets
  unless an explicit advanced setting permits them.

## Operations And UI State

- Failed, unsubmitted, and skipped-not-open chapters feed a manual intervention
  projection. Marking an item resolved appends an immutable operator audit row
  and structured event; it does not rewrite the chapter or task result.
- The authenticated operations endpoint samples CPU, memory, optional hardware
  temperature, process capacity, active task IDs, durable runs, heartbeat age,
  lease expiry, and stale-run state. Unavailable platform metrics remain
  explicit instead of being fabricated.
- Event archive is a non-destructive visibility stage until the retention
  process reaches the later deletion cutoff. The UI can archive by date with
  an explicit confirmation; no unarchive operation is currently exposed.
- Theme preference is client-only state with `system`, `light`, and `dark`
  values stored under `cx.theme`. A pre-mount script applies the resolved theme
  before Vue renders; system mode follows `prefers-color-scheme`, and reduced
  motion/transparency preferences are respected.
- Playwright exercises initialization, authentication, all primary routes,
  mobile overflow, and theme persistence/system resolution against mocked API
  boundaries. CI installs Chromium and runs this suite after type-check, lint,
  and production build.

## Legacy Coexistence Strategy

1. Freeze legacy response fixtures after removing personal data.
2. Keep root legacy code outside every new runtime entry point; use it only for
   protocol comparison and sanitized regression fixtures.
3. Initialize the new database through Alembic, never by pointing the new
   service at the legacy database.
4. Preview supported account/settings migration with `import-legacy`, review
   every warning, then explicitly apply it while both applications are offline.
5. Re-enter unsupported integration configuration and retain legacy history as
   a separate read-only archive when required.
6. Remove legacy paths only after the acceptance matrix and target-environment
   deployment checks have been reviewed.
