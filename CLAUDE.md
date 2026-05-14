# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt

# Web mode (recommended) — Flask on :5002
python server.py

# CLI mode — single account, interactive or config-driven
python main.py                                          # interactive
python main.py -c config.ini                            # config file
python main.py -u <phone> -p <password> -l <id1,id2>   # CLI args

# Tests
python -m unittest tests/test_regressions.py

# Docker
docker build -t chaoxing . && docker run -it -v /path/to/config.ini:/config/config.ini chaoxing
docker compose up -d --build   # Web mode with persistent ./data/
```

## Architecture

### Two entry points

- **`server.py`** — Web mode. Calls `create_app(reconcile_tasks=False)`, then starts Flask with `threaded=True`. Only reconciles incomplete tasks when run as `__main__`.
- **`main.py`** — CLI mode. Parses args/config, creates a single `Chaoxing` + `Account`, logs in, filters courses, then runs `process_course` per course.

### `api/` — platform interaction layer

**[api/base.py](api/base.py)** — `Chaoxing` owns all HTTP calls to the platform (`study_video`, `study_document`, `study_work`, `study_read`). `SessionManager` is a singleton `requests.Session` factory. `StudyResult` enum returned by every `study_*` method. `Account` holds per-account credentials, cookies, and UA string.

**[api/answer.py](api/answer.py)** — `Tiku` abstract base + subclasses: `TikuYanxi`, `TikuLike`, `TikuAdapter`, `AI` (OpenAI-compatible), `SiliconFlow`, `EnsembleTiku` (multi-model with referee voting). `CacheDAO` persists answers to `cache.json` (with corrupt-file recovery).

**[api/decode.py](api/decode.py)** — Parses raw API JSON into structured dicts for courses, chapter points, and questions.

**[api/font_decoder.py](api/font_decoder.py) / [api/cxsecret_font.py](api/cxsecret_font.py)** — The platform obfuscates text with custom TTF fonts; these decode glyph mappings using `fonttools` and [resource/font_map_table.json](resource/font_map_table.json).

**[api/web_search.py](api/web_search.py)** — `WebSearch` abstract base with `DuckDuckGoSearch` (free, HTML scraping) and `CustomAPISearch`. Provides search context to AI answer models.

**[api/cipher.py](api/cipher.py)** — AES-CBC encryption for login. Key stored in `api/config.py:GlobalConst.AESKey`.

**[api/captcha.py](api/captcha.py)** — Image captcha solving via `ddddocr`.

**[api/cookies.py](api/cookies.py)** — `parse_cookie_string` / `dump_cookie_string` for cookie serialization; `save_cookies` / `use_cookies` for file persistence.

**[api/exceptions.py](api/exceptions.py)** — `LoginError`, `InputFormatError`, `MaxRetryExceeded`, `MaxRollBackExceeded`, `FontDecodeError`.

**[api/answer_check.py](api/answer_check.py)** — Answer comparison utilities (the `cut` function used when matching answers).

**[api/process.py](api/process.py)** — CLI progress bar rendering (`show_progress`, `sec2time`).

### `web/` — Flask management backend

**[web/__init__.py](web/__init__.py)** — `create_app(reconcile_tasks=False)` factory. Registers two Blueprints and serves `static/index.html` as the SPA catch-all.

**[web/models.py](web/models.py)** — SQLite data layer (DB path overridable via `DB_PATH` env var). Tables: `users`, `study_tasks`, `chapter_logs`, `settings` (JSON key-value), `operation_logs`. All timestamps use `now_iso()` which respects the configured timezone (default `Asia/Shanghai`, overridable via `APP_TIMEZONE`). Settings include: `timezone`, `max_concurrent_accounts`, `course_progress_workers`, `show_system_metrics`, `run_window_enabled/start/end`.

**[web/tasks.py](web/tasks.py)** — Multi-account task scheduler. Key design:
- Uses `multiprocessing.get_context("spawn")` — each task runs in a **separate process**, not a thread
- Same-account tasks are mutually exclusive (`_active_user_ids` set); `max_concurrent_accounts` caps total concurrent accounts
- `_scheduler_cond` (threading.Condition) coordinates the pending → active transition
- Stop events (`_stop_events` dict) allow force-stopping a running task process
- Live log broadcast: 200-entry ring buffer pushed to SSE subscriber queues

**[web/routes/api.py](web/routes/api.py)** — ~30 REST endpoints: user CRUD + CSV import, course list + progress fetch, study start/stop, queue pause/resume/clear, settings get/set, intervention records, dashboard data.

**[web/routes/sse.py](web/routes/sse.py)** — SSE endpoint for chapter progress streaming. Polls the DB every 1s (not in-memory queues) for Raspberry Pi deployment stability. Sends 30s keepalive comments.

**[web/tiku_config.py](web/tiku_config.py)** — `build_effective_tiku_config(user_config, settings)` merges per-user tiku config with global settings. `AI` provider models inherit global endpoint/key/model when user leaves them blank.

**[web/system_metrics.py](web/system_metrics.py)** — Reads `/proc/stat`, `/proc/meminfo`, `/sys/class/thermal/thermal_zone0/temp` for dashboard system health. Detects Raspberry Pi via `platform.machine()`.

### `app.py`

Celery scaffold — defines `celery_init_app` with SQLite broker/result backend. Not wired into `server.py`; appears to be work-in-progress infrastructure for async task execution.

### CLI processing flow ([main.py](main.py))

`main()` → `init_config()` (args or .ini) → `init_chaoxing()` (Account + Tiku) → login → `get_course_list()` → `filter_courses()` → per-course: `process_course()` → `JobProcessor` (PriorityQueue + thread pool per course) → per-chapter: `process_chapter()` → per-job: `ThreadPoolExecutor` of `process_job()`.

`ChapterResult` enum: `SUCCESS`, `ERROR`, `NOT_OPEN`, `PENDING`, `STOPPED`. `JobProcessor` supports retry (max 5), not-open handling (retry/continue), and graceful stop via a `should_stop` callback.

## Configuration

Copy `config_template.ini` → `config.ini`. Key sections:
- `[common]` — credentials, course list, speed (clamped 1–2×), parallel jobs, `notopen_action` (retry/continue), `user_agent`
- `[tiku]` — answer provider, tokens, `multi_model`, `submit` mode, `cover_rate`
- `[notification]` — push service and URL

Cookies login (CLI): set `use_cookies=true` and place cookies in `cookies.txt`.

Logs go to `chaoxing.log` (10 MB rotation) and stdout via a tqdm-safe loguru sink.

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `DB_PATH` | SQLite database path | `chaoxing_web.db` |
| `CACHE_PATH` | Answer cache JSON path | `cache.json` |
| `APP_TIMEZONE` | Timezone for timestamps | `Asia/Shanghai` |
