import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DB_PATH = os.environ.get("DB_PATH", "chaoxing_web.db")
DEFAULT_SETTINGS = {
    "timezone": os.environ.get("APP_TIMEZONE", "Asia/Shanghai"),
    "max_concurrent_accounts": 2,
    "course_progress_workers": 3,
    "show_system_metrics": True,
    "scheduler_paused": False,
    "run_window_enabled": False,
    "run_window_start": "08:00",
    "run_window_end": "23:00",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
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
CREATE TABLE IF NOT EXISTS study_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  course_id TEXT,
  course_title TEXT,
  status TEXT DEFAULT 'pending',
  started_at TEXT,
  finished_at TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS chapter_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  chapter_title TEXT,
  result TEXT,
  message TEXT,
  cleared INTEGER DEFAULT 0,
  ts TEXT,
  FOREIGN KEY(task_id) REFERENCES study_tasks(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS operation_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT DEFAULT 'system',
  message TEXT,
  ts TEXT
);
"""

def _ensure_column(conn, table, col, col_def):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")

def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _ensure_column(conn, "users", "user_agent", "TEXT DEFAULT ''")
        _ensure_column(conn, "chapter_logs", "cleared", "INTEGER DEFAULT 0")


def _normalize_setting_value(key, value):
    if key in {"max_concurrent_accounts", "course_progress_workers"}:
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = DEFAULT_SETTINGS[key]
        return max(1, value)
    if key in {"show_system_metrics", "scheduler_paused", "run_window_enabled"}:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if key in {"run_window_start", "run_window_end"}:
        value = str(value or DEFAULT_SETTINGS[key]).strip()
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return DEFAULT_SETTINGS[key]
        hour = max(0, min(23, int(parts[0])))
        minute = max(0, min(59, int(parts[1])))
        return f"{hour:02d}:{minute:02d}"
    if key == "timezone":
        value = str(value or DEFAULT_SETTINGS["timezone"]).strip()
        return value or DEFAULT_SETTINGS["timezone"]
    return value


def _safe_timezone_name(name):
    try:
        ZoneInfo(name)
        return name
    except ZoneInfoNotFoundError:
        return DEFAULT_SETTINGS["timezone"]


def _get_timezone(conn=None):
    name = get_timezone_name(conn)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        try:
            return datetime.now().astimezone().tzinfo or timezone.utc
        except Exception:
            return timezone.utc


def get_timezone_name(conn=None):
    if conn is not None:
        raw = _get_setting_value(conn, "timezone", DEFAULT_SETTINGS["timezone"])
        return _safe_timezone_name(_normalize_setting_value("timezone", raw))

    with get_conn() as inner_conn:
        return get_timezone_name(inner_conn)


def now_iso(conn=None):
    return datetime.now(_get_timezone(conn)).isoformat()


def parse_timestamp(value, conn=None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    target_tz = _get_timezone(conn)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=target_tz)
    return parsed.astimezone(target_tz)


def _get_setting_value(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    value = row["value"]
    try:
        return json.loads(value)
    except Exception:
        return value

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def _parse(row):
    if row is None:
        return None
    d = dict(row)
    for k in ('tiku_config', 'notification_config'):
        if k in d and d[k]:
            try:
                d[k] = json.loads(d[k])
            except Exception:
                d[k] = {}
    return d

def get_users():
    with get_conn() as conn:
        return [_parse(r) for r in conn.execute("SELECT * FROM users ORDER BY id")]

def get_user(uid):
    with get_conn() as conn:
        return _parse(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())

def create_user(data):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (username,password,use_cookies,speed,jobs,notopen_action,tiku_config,notification_config,remark,user_agent,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (data['username'], data.get('password',''), data.get('use_cookies',0),
             data.get('speed',1.0), data.get('jobs',4), data.get('notopen_action','retry'),
             json.dumps(data.get('tiku_config',{})), json.dumps(data.get('notification_config',{})),
             data.get('remark',''), data.get('user_agent',''),
             now_iso(conn))
        )

def update_user(uid, data):
    fields = ['password','use_cookies','cookies_data','speed','jobs','notopen_action','tiku_config','notification_config','enabled','remark','user_agent']
    updates = {}
    for f in fields:
        if f in data:
            updates[f] = json.dumps(data[f]) if f in ('tiku_config','notification_config') else data[f]
    if not updates:
        return
    sql = "UPDATE users SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=?"
    with get_conn() as conn:
        conn.execute(sql, list(updates.values()) + [uid])

def delete_user(uid):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))

def create_task(user_id, course_id, course_title):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO study_tasks (user_id,course_id,course_title,status,started_at,finished_at) VALUES (?,?,?,'pending',NULL,NULL)",
            (user_id, course_id, course_title)
        )
        return cur.lastrowid

def update_task_status(task_id, status):
    with get_conn() as conn:
        now = now_iso(conn)
        if status == 'running':
            conn.execute(
                "UPDATE study_tasks SET status=?,started_at=COALESCE(started_at,?),finished_at=NULL WHERE id=?",
                (status, now, task_id),
            )
        elif status in ('done','error','stopped'):
            conn.execute("UPDATE study_tasks SET status=?,finished_at=? WHERE id=?", (status, now, task_id))
        else:
            conn.execute("UPDATE study_tasks SET status=? WHERE id=?", (status, task_id))


def reconcile_incomplete_tasks():
    with get_conn() as conn:
        now = now_iso(conn)
        conn.execute(
            "UPDATE study_tasks SET status='stopped', finished_at=COALESCE(finished_at, ?) "
            "WHERE status IN ('pending', 'running')",
            (now,),
        )

def get_tasks(limit=50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT t.*,u.username FROM study_tasks t LEFT JOIN users u ON t.user_id=u.id ORDER BY t.id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

def get_task(task_id):
    with get_conn() as conn:
        return _parse(conn.execute("SELECT * FROM study_tasks WHERE id=?", (task_id,)).fetchone())

def add_chapter_log(task_id, chapter_title, result, message=''):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chapter_logs (task_id,chapter_title,result,message,cleared,ts) VALUES (?,?,?,?,?,?)",
            (task_id, chapter_title, result, message, 0, now_iso(conn))
        )

def get_chapter_logs(task_id, after_id=0):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM chapter_logs WHERE task_id=? AND id>? ORDER BY id",
            (task_id, after_id)
        ).fetchall()]

def get_recent_logs(limit=100):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT cl.*,st.course_title,u.username FROM chapter_logs cl "
            "JOIN study_tasks st ON cl.task_id=st.id "
            "JOIN users u ON st.user_id=u.id "
            "ORDER BY cl.id DESC LIMIT ?", (limit,)
        ).fetchall()]

def get_progress():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT u.username, st.course_title, st.status, st.started_at, st.finished_at, "
            "COUNT(cl.id) as total_chapters, "
            "SUM(CASE WHEN cl.result='success' THEN 1 ELSE 0 END) as done_chapters "
            "FROM study_tasks st JOIN users u ON st.user_id=u.id "
            "LEFT JOIN chapter_logs cl ON cl.task_id=st.id "
            "GROUP BY st.id ORDER BY st.id DESC LIMIT 100"
        ).fetchall()]

def get_settings():
    with get_conn() as conn:
        result = {}
        for r in conn.execute("SELECT key,value FROM settings").fetchall():
            try:
                result[r['key']] = json.loads(r['value'])
            except Exception:
                result[r['key']] = r['value']
        for key, value in DEFAULT_SETTINGS.items():
            result.setdefault(key, value)
        for key in list(result):
            result[key] = _normalize_setting_value(key, result[key])
        result["timezone"] = _safe_timezone_name(result["timezone"])
        return result

def add_operation_log(category, message):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO operation_logs (category,message,ts) VALUES (?,?,?)",
            (category, message, now_iso(conn))
        )

def get_operation_logs(limit=200):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM operation_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]

def get_intervention_needed():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT cl.*,st.course_title,u.username FROM chapter_logs cl "
            "JOIN study_tasks st ON cl.task_id=st.id "
            "JOIN users u ON st.user_id=u.id "
            "WHERE cl.result IN ('error','skipped','unsubmitted') AND cl.chapter_title != '' "
            "AND COALESCE(cl.cleared, 0)=0 "
            "ORDER BY cl.id DESC LIMIT 100"
        ).fetchall()]


def clear_intervention(log_id):
    with get_conn() as conn:
        conn.execute("UPDATE chapter_logs SET cleared=1 WHERE id=?", (log_id,))


def clear_all_interventions():
    with get_conn() as conn:
        conn.execute(
            "UPDATE chapter_logs SET cleared=1 "
            "WHERE result IN ('error','skipped','unsubmitted') AND chapter_title != ''"
        )


def count_tasks_completed_today():
    with get_conn() as conn:
        today = datetime.now(_get_timezone(conn)).date()
        rows = conn.execute(
            "SELECT finished_at FROM study_tasks WHERE status='done' AND finished_at IS NOT NULL"
        ).fetchall()
        count = 0
        for row in rows:
            finished_at = parse_timestamp(row["finished_at"], conn)
            if finished_at and finished_at.date() == today:
                count += 1
        return count

def set_settings(data):
    with get_conn() as conn:
        for k, v in data.items():
            if k in DEFAULT_SETTINGS:
                v = _normalize_setting_value(k, v)
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                         (k, json.dumps(v) if not isinstance(v, str) else v))
