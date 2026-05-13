import os
import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "chaoxing_web.db")

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
             datetime.now().isoformat())
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
    now = datetime.now().isoformat()
    with get_conn() as conn:
        if status == 'running':
            conn.execute(
                "UPDATE study_tasks SET status=?,started_at=COALESCE(started_at,?),finished_at=NULL WHERE id=?",
                (status, now, task_id),
            )
        elif status in ('done','error','stopped'):
            conn.execute("UPDATE study_tasks SET status=?,finished_at=? WHERE id=?", (status, now, task_id))
        else:
            conn.execute("UPDATE study_tasks SET status=? WHERE id=?", (status, task_id))

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
            "INSERT INTO chapter_logs (task_id,chapter_title,result,message,ts) VALUES (?,?,?,?,?)",
            (task_id, chapter_title, result, message, datetime.now().isoformat())
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
        return result

def add_operation_log(category, message):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO operation_logs (category,message,ts) VALUES (?,?,?)",
            (category, message, datetime.now().isoformat())
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
            "ORDER BY cl.id DESC LIMIT 100"
        ).fetchall()]

def set_settings(data):
    with get_conn() as conn:
        for k, v in data.items():
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                         (k, json.dumps(v) if not isinstance(v, str) else v))
