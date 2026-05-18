# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt   # or: pip install .

python main.py                                          # interactive
python main.py -c config.ini                            # config file
python main.py -u <phone> -p <password> -l <id1,id2>   # CLI args

docker build -t chaoxing . && docker run -it -v /path/to/config.ini:/config/config.ini chaoxing
```

## Architecture

`main.py` is the CLI entry point. It parses args or a `.ini` config, then:
1. Instantiates `Chaoxing` ([api/base.py](api/base.py)) with an `Account` and a `Tiku` answer provider
2. Logs in, fetches the course list, filters to requested courses
3. For each course: `process_course` → `JobProcessor` runs chapters concurrently via `PriorityQueue` + thread pool; each chapter's task points run in a nested `ThreadPoolExecutor`

**[api/base.py](api/base.py)** — `Chaoxing` owns all HTTP calls to the platform (`study_video`, `study_document`, `study_work`, `study_read`). `SessionManager` is a singleton `requests.Session`. `StudyResult` is returned by every `study_*` method.

**[api/answer.py](api/answer.py)** — `Tiku` abstract base + concrete subclasses: `TikuYanxi`, `TikuLike`, `TikuAdapter`, `AI` (OpenAI-compatible), `SiliconFlow`. `CacheDAO` persists answers to `cache.json`.

**[api/notification.py](api/notification.py)** — `NotificationService` base with `ServerChan`, `Qmsg`, `Bark`, `Telegram` subclasses.

**[api/decode.py](api/decode.py)** — Parses raw API JSON into structured dicts for courses, chapter points, and questions.

**[api/font_decoder.py](api/font_decoder.py) / [api/cxsecret_font.py](api/cxsecret_font.py)** — The platform obfuscates text with custom TTF fonts; these decode glyph mappings using `fonttools` and [resource/font_map_table.json](resource/font_map_table.json).

**[api/cipher.py](api/cipher.py)** — AES encryption used during login.

**[api/captcha.py](api/captcha.py)** — Solves image captchas via `ddddocr`.

## Configuration

Copy `config_template.ini` → `config.ini`. Key sections:
- `[common]` — credentials, course list, speed (1–2×), parallel jobs, `notopen_action` (retry/continue)
- `[tiku]` — answer provider, tokens, `submit` mode, `cover_rate`
- `[notification]` — push service and URL

Cookies login: set `use_cookies=true` and place cookies in `cookies.txt`.

Logs go to `chaoxing.log` (10 MB rotation) and stdout via a tqdm-safe sink.
