# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt   # or: pip install .

# CLI mode (single account, original interface)
python main.py                                          # interactive
python main.py -c config.ini                            # config file
python main.py -u <phone> -p <password> -l <id1,id2>   # CLI args

# Web admin mode (recommended — multi-account dashboard)
python server.py                      # Flask on http://localhost:5002

# Docker
docker compose up -d --build          # Web mode via docker-compose
docker build -t chaoxing . && docker run -it -v /path/to/config.ini:/config/config.ini chaoxing  # CLI mode

# Tests
python -m pytest tests/ -v
python -m unittest tests/test_regressions.py
```

## Architecture

### Entry points

- **[main.py](main.py)** — CLI entry. Parses args/config, creates `Chaoxing` + `Tiku`, fetches courses, then `process_course` → `JobProcessor` runs chapters concurrently via `PriorityQueue` + thread pool; each chapter's task points run in a nested `ThreadPoolExecutor`. Returns `ChapterResult` enum values to callbacks.
- **[server.py](server.py)** → **[web/__init__.py](web/__init__.py)** — Flask factory (`create_app`), registers `api_bp` and `sse_bp` blueprints, serves SPA from `web/static/`.

### Web layer (multi-account dashboard)

**[web/models.py](web/models.py)** — SQLite schema (`chaoxing_web.db`): `users`, `study_tasks`, `chapter_logs`, `settings`, `operation_logs`. Each user has independent tiku/AI config, speed, jobs, notification settings.

**[web/tasks.py](web/tasks.py)** — Task queue (`queue.Queue`) with a single-threaded worker that processes study tasks sequentially, one per user. Each task gets a `threading.Event` for stop signaling. SSE channels per-task (`subscribe_sse` / `_emit`) and a global live-log broadcast (`_broadcast_log`) captured from `loguru`.

**[web/routes/api.py](web/routes/api.py)** — REST API: user CRUD, CSV import, course/point listing, study start/stop, settings, intervention list.
**[web/routes/sse.py](web/routes/sse.py)** — Two SSE endpoints: `/api/stream/<task_id>` (per-task chapter progress) and `/api/log-stream` (global terminal log mirror). Uses keepalive pings every 30s.

**[web/static/](web/static/)** — Vanilla SPA: `index.html`, `app.js`, `style.css`. Deep/light theme, real-time SSE log panel.

### Core API layer

**[api/base.py](api/base.py)** — `Chaoxing` owns all HTTP calls (`study_video`, `study_document`, `study_work`, `study_read`). `SessionManager` creates isolated `requests.Session` per account (singleton per-Chaoxing). `StudyResult` enum (SUCCESS/FORBIDDEN/ERROR/TIMEOUT/STOPPED) returned by every study method. `RateLimiter` throttles video progress reports (2s) and general API calls (0.5s). `Account` dataclass holds credentials, optional `cookies_data`, and `user_agent`.

**[api/answer.py](api/answer.py)** — `Tiku` abstract base with `_query(q_info)` interface. Concrete providers: `TikuYanxi` (言溪), `TikuLike` (Like知识库), `TikuAdapter`, `AI` (OpenAI-compatible), `SiliconFlow` (硅基流动). `CacheDAO` persists answers to `cache.json` (with corruption recovery).

**`EnsembleTiku`** (in [api/answer.py](api/answer.py)) — Multi-model collaborative answering (v1.5 headline feature). Accepts a JSON `models` config array with per-model provider/endpoint/key. Runs up to 3 models in parallel via `ThreadPoolExecutor`. If answers disagree, a designated referee model (by index) re-queries with candidate answers as context and decides. Optionally injects web search results into the prompt.

**[api/web_search.py](api/web_search.py)** — Search abstraction: `WebSearch` ABC → `DuckDuckGoSearch` (free, parses HTML) + `CustomSearch` (user-supplied API endpoint). Factory `create_search(config)` wires it into `EnsembleTiku`.

**[api/decode.py](api/decode.py)** — Parses raw platform API JSON into structured dicts for courses, chapter points, and questions.

**[api/font_decoder.py](api/font_decoder.py) / [api/cxsecret_font.py](api/cxsecret_font.py)** — The platform obfuscates text with custom TTF fonts; these decode glyph mappings using `fonttools` and [resource/font_map_table.json](resource/font_map_table.json).

**[api/cipher.py](api/cipher.py)** — AES-CBC encryption for login password.
**[api/captcha.py](api/captcha.py)** — Solves image captchas via `ddddocr`.
**[api/cookies.py](api/cookies.py)** — Cookie serialization: `parse_cookie_string`, `dump_cookie_string`, `save_cookies`, `use_cookies`.
**[api/exceptions.py](api/exceptions.py)** — `LoginError`, `InputFormatError`, `MaxRollBackExceeded`, `MaxRetryExceeded`, `FontDecodeError`.
**[api/notification.py](api/notification.py)** — `NotificationService` base with `ServerChan`, `Qmsg`, `Bark`, `Telegram` subclasses.
**[api/process.py](api/process.py)** — `sec2time` / `show_progress` utilities for CLI progress bars.

### Flow: Web study task lifecycle

1. Frontend POSTs `/api/study/start` with `user_id`, `courses[]`, optional `chapter_ids`
2. `start_task()` creates a DB row (`study_tasks`) and enqueues the task
3. `_task_worker` (single daemon thread) dequeues and calls `_run_task()`
4. `_run_task()`: builds `Chaoxing` from user DB config → logs in → fetches course → optionally filters chapter points → calls `main.process_course()` with an `on_chapter_complete` callback
5. The callback `_emit()`s results to both `chapter_logs` DB table and per-task SSE queues
6. `_broadcast_log()` pushes log lines to all live-log SSE subscribers and a 200-entry ring buffer
7. Stop signal: `POST /api/study/stop/<task_id>` sets the task's `threading.Event`, which `JobProcessor` checks before processing each chapter

## Configuration

Copy `config_template.ini` → `config.ini`. Key sections:
- `[common]` — credentials, course list, speed (1–2×), parallel jobs, `notopen_action` (retry/continue), `user_agent`
- `[tiku]` — answer provider, tokens, `multi_model`, `submit` mode, `cover_rate`
- `[notification]` — push service and URL

Cookies login: set `use_cookies=true` and place cookies in `cookies.txt`.

In web mode, each user's config is stored in the SQLite `users.tiku_config` JSON column and managed through the UI.

Logs go to `chaoxing.log` (10 MB rotation) and stdout via a `loguru` tqdm-safe sink. In web mode, logs are also broadcast via SSE to the dashboard terminal panel.

Data files excluded from git: `config.ini`, `cookies.txt`, `chaoxing.log`, `cache.json`, `chaoxing_web.db`.
