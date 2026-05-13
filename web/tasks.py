import os
import sys
import threading
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.answer import Tiku
from api.base import Account, Chaoxing
from api.exceptions import LoginError
from api.logger import logger
from web.models import (
    add_chapter_log,
    add_operation_log,
    create_task,
    get_settings,
    get_timezone_name,
    get_user,
    now_iso,
    set_settings,
    update_task_status,
)
from web.tiku_config import build_effective_tiku_config

# task_id -> threading.Event (set to stop)
_stop_events: dict[int, threading.Event] = {}
# task_id -> list of SSE queues (for chapter progress)
_sse_queues: dict[int, list] = {}
# Global live log subscribers for web UI broadcast
_live_log_subscribers: list = []          # list of queue.Queue, one per SSE client
_live_log_lock = threading.Lock()
_live_log_history: list = []              # ring buffer of recent messages (max 200)

# Multi-account scheduler state
_pending_tasks: list[dict] = []
_active_tasks: dict[int, dict] = {}
_active_user_ids: set[int] = set()
_scheduler_running = False
_scheduler_cond = threading.Condition()


def _ts():
    return now_iso()


def _broadcast_log(payload):
    """Push a log message to all active subscribers and ring buffer."""
    with _live_log_lock:
        _live_log_history.append(payload)
        if len(_live_log_history) > 200:
            _live_log_history.pop(0)
        dead = []
        for q in _live_log_subscribers:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            _live_log_subscribers.remove(q)


def subscribe_live_log(q):
    """Register a subscriber queue. Returns a copy of recent history."""
    with _live_log_lock:
        _live_log_subscribers.append(q)
        return list(_live_log_history)


def unsubscribe_live_log(q):
    """Remove a subscriber queue."""
    with _live_log_lock:
        try:
            _live_log_subscribers.remove(q)
        except ValueError:
            pass


def _loguru_sink(msg):
    """Push formatted loguru message to the live log broadcast."""
    payload = {
        'category': 'log',
        'message': str(msg).rstrip(),
        'ts': _ts(),
    }
    _broadcast_log(payload)


_loguru_sink_id = getattr(logger, '_web_sink_id', None)
if _loguru_sink_id is None:
    _loguru_sink_id = logger.add(_loguru_sink, level="DEBUG", format="{time:HH:mm:ss.SSS} | {message}")
    logger._web_sink_id = _loguru_sink_id


def subscribe_sse(task_id, q):
    _sse_queues.setdefault(task_id, []).append(q)


def unsubscribe_sse(task_id, q):
    try:
        _sse_queues.get(task_id, []).remove(q)
    except ValueError:
        pass


def _emit(task_id, chapter_title, result, message=''):
    add_chapter_log(task_id, chapter_title, result, message)
    payload = {'chapter_title': chapter_title, 'result': result, 'message': message, 'ts': _ts()}
    for q in list(_sse_queues.get(task_id, [])):
        try:
            q.put_nowait(payload)
        except Exception:
            pass


def _emit_live(category, message):
    """推送实时日志到 web UI"""
    payload = {'category': category, 'message': message, 'ts': _ts()}
    add_operation_log(category, message)
    _broadcast_log(payload)


def _get_max_concurrent_accounts():
    settings = get_settings()
    try:
        return max(1, int(settings.get('max_concurrent_accounts', 2)))
    except (TypeError, ValueError):
        return 2


def _parse_clock(value: str, default: str) -> dt_time:
    raw = str(value or default).strip()
    try:
        hour_text, minute_text = raw.split(":", 1)
        return dt_time(hour=int(hour_text), minute=int(minute_text))
    except Exception:
        fallback_hour, fallback_minute = default.split(":", 1)
        return dt_time(hour=int(fallback_hour), minute=int(fallback_minute))


def _get_current_time():
    try:
        tz = ZoneInfo(get_timezone_name())
    except ZoneInfoNotFoundError:
        tz = datetime.now().astimezone().tzinfo
    return datetime.now(tz).time()


def _is_within_run_window(settings=None):
    settings = settings or get_settings()
    if not settings.get('run_window_enabled'):
        return True

    start = _parse_clock(settings.get('run_window_start', '08:00'), '08:00')
    end = _parse_clock(settings.get('run_window_end', '23:00'), '23:00')
    now_time = _get_current_time()

    if start == end:
        return True
    if start < end:
        return start <= now_time < end
    return now_time >= start or now_time < end


def _scheduler_can_run(settings=None):
    settings = settings or get_settings()
    return not settings.get('scheduler_paused') and _is_within_run_window(settings)


def get_scheduler_stats():
    settings = get_settings()
    with _scheduler_cond:
        return {
            'pending': len(_pending_tasks),
            'active': len(_active_tasks),
            'active_users': len(_active_user_ids),
            'max_concurrent_accounts': _get_max_concurrent_accounts(),
            'paused': bool(settings.get('scheduler_paused')),
            'run_window_enabled': bool(settings.get('run_window_enabled')),
            'run_window_start': settings.get('run_window_start', '08:00'),
            'run_window_end': settings.get('run_window_end', '23:00'),
            'within_run_window': _is_within_run_window(settings),
            'can_start_new_tasks': _scheduler_can_run(settings),
        }


def _pick_schedulable_task_locked():
    if not _scheduler_can_run():
        return None

    if len(_active_tasks) >= _get_max_concurrent_accounts():
        return None

    for index, task in enumerate(_pending_tasks):
        if task['user_id'] in _active_user_ids:
            continue
        return _pending_tasks.pop(index)

    return None


def _build_chaoxing(user: dict) -> Chaoxing:
    tiku_conf = build_effective_tiku_config(user.get('tiku_config') or {}, get_settings())
    multi_model = tiku_conf.get('multi_model', 'false') in ('true', 'True', '1', 'yes')
    if multi_model:
        from api.answer import EnsembleTiku

        tiku = EnsembleTiku()
        tiku.config_set(tiku_conf)
        tiku.init_tiku()
    else:
        tiku = Tiku()
        tiku.config_set(tiku_conf)
        tiku = tiku.get_tiku_from_config()
        tiku.init_tiku()
    account = Account(
        user['username'],
        user.get('password', ''),
        cookies_data=user.get('cookies_data') or "",
        user_agent=user.get('user_agent') or None,
        use_cookie_file=False,
    )
    return Chaoxing(account=account, tiku=tiku, query_delay=float(tiku_conf.get('delay', 0)))


def _run_task(task_id: int, user_id: int, course_id: str, course_title: str, stop_event: threading.Event,
              chapter_ids: list = None):
    update_task_status(task_id, 'running')
    _emit_live('study', f'开始学习: {course_title}')
    try:
        user = get_user(user_id)
        cx = _build_chaoxing(user)
        cx._task_stop_event = stop_event
        result = cx.login(login_with_cookies=bool(user.get('use_cookies')))
        if not result['status']:
            _emit_live('login', f'用户 {user["username"]} 登录失败: {result["msg"]}')
            raise LoginError(result['msg'])
        if getattr(cx.account, 'cookies_data', None):
            from web.models import update_user

            update_user(user_id, {'cookies_data': cx.account.cookies_data})
        _emit_live('login', f'用户 {user["username"]} 登录成功')

        all_courses = cx.get_course_list()
        course = next((c for c in all_courses if c['courseId'] == course_id), None)
        if not course:
            raise ValueError(f"Course {course_id} not found")

        config = {
            'speed': float(user.get('speed', 1.0)),
            'jobs': int(user.get('jobs', 4)),
            'notopen_action': user.get('notopen_action', 'retry'),
            'should_stop': stop_event.is_set,
        }

        if chapter_ids:
            point_list = cx.get_course_point(course['courseId'], course['clazzId'], course['cpi'])
            all_points = point_list.get('points', [])
            selected = [p for p in all_points if p['id'] in chapter_ids]
            _emit_live('study', f'选择了 {len(selected)}/{len(all_points)} 个章节')
            original_gcp = cx.get_course_point

            def filtered_gcp(*args, **kwargs):
                result = original_gcp(*args, **kwargs)
                result['points'] = [p for p in result.get('points', []) if p['id'] in chapter_ids]
                return result

            cx.get_course_point = filtered_gcp

        import main as main_mod
        from main import ChapterResult

        def on_chapter_complete(chapter_title, result, message):
            """Callback invoked by process_chapter for each chapter completion."""
            if result == ChapterResult.SUCCESS:
                if getattr(cx, '_last_quiz_low_coverage', False):
                    _emit(task_id, chapter_title, 'unsubmitted', '题库覆盖率不足，仅保存未提交')
                    _emit_live('study', f'需人工接管(未提交): {chapter_title}')
                else:
                    _emit(task_id, chapter_title, 'success')
                    _emit_live('study', f'完成: {chapter_title}')
            elif result == ChapterResult.NOT_OPEN:
                _emit(task_id, chapter_title, 'skipped', 'Not open')
                _emit_live('study', f'跳过(未开放): {chapter_title}')
            elif result == ChapterResult.STOPPED:
                _emit(task_id, chapter_title, 'skipped', message or 'Stopped by user')
                _emit_live('study', f'已停止: {chapter_title}')
            else:
                _emit(task_id, chapter_title, 'error', message or 'Unknown error')
                _emit_live('study', f'失败: {chapter_title}')

        try:
            main_mod.process_course(cx, course, config, on_chapter_complete=on_chapter_complete)
        finally:
            if chapter_ids:
                cx.get_course_point = original_gcp

        status = 'stopped' if stop_event.is_set() else 'done'
        update_task_status(task_id, status)
        _emit_live('study', f'学习结束({status}): {course_title}')
    except Exception as e:
        _emit(task_id, '', 'error', str(e))
        _emit_live('error', f'{course_title}: {e}')
        update_task_status(task_id, 'error')
    finally:
        _stop_events.pop(task_id, None)
        for q in list(_sse_queues.get(task_id, [])):
            try:
                q.put_nowait(None)
            except Exception:
                pass


def _task_runner(task: dict, stop_event: threading.Event):
    try:
        _run_task(
            task['task_id'],
            task['user_id'],
            task['course_id'],
            task['course_title'],
            stop_event,
            task.get('chapter_ids'),
        )
    finally:
        with _scheduler_cond:
            _active_tasks.pop(task['task_id'], None)
            _active_user_ids.discard(task['user_id'])
            _scheduler_cond.notify_all()


def _scheduler_loop():
    global _scheduler_running
    _emit_live('system', '任务调度器已启动')
    try:
        while True:
            with _scheduler_cond:
                task = _pick_schedulable_task_locked()
                if task is None:
                    _scheduler_cond.wait(timeout=1)
                    continue

                stop_event = threading.Event()
                _stop_events[task['task_id']] = stop_event
                _active_user_ids.add(task['user_id'])
                thread = threading.Thread(target=_task_runner, args=(task, stop_event), daemon=True)
                _active_tasks[task['task_id']] = {
                    'thread': thread,
                    'user_id': task['user_id'],
                    'course_title': task['course_title'],
                }

            _emit_live('system', f'开始执行: #{task["task_id"]} {task["course_title"]}')
            thread.start()
    finally:
        with _scheduler_cond:
            _scheduler_running = False


def _ensure_scheduler():
    global _scheduler_running
    with _scheduler_cond:
        if _scheduler_running:
            return
        _scheduler_running = True
        thread = threading.Thread(target=_scheduler_loop, daemon=True)
        thread.start()


def set_scheduler_paused(paused: bool):
    set_settings({'scheduler_paused': paused})
    with _scheduler_cond:
        _scheduler_cond.notify_all()


def start_task(user_id: int, course_id: str, course_title: str, chapter_ids: list = None) -> int:
    task_id = create_task(user_id, course_id, course_title)
    _ensure_scheduler()
    with _scheduler_cond:
        _pending_tasks.append({
            'task_id': task_id,
            'user_id': user_id,
            'course_id': course_id,
            'course_title': course_title,
            'chapter_ids': chapter_ids,
        })
        _scheduler_cond.notify_all()
    _emit_live('system', f'任务已加入队列: #{task_id} {course_title}')
    return task_id


def stop_task(task_id: int):
    with _scheduler_cond:
        for index, task in enumerate(_pending_tasks):
            if task['task_id'] != task_id:
                continue
            _pending_tasks.pop(index)
            update_task_status(task_id, 'stopped')
            _emit_live('system', f'任务 #{task_id} 已从队列移除')
            _scheduler_cond.notify_all()
            return

    ev = _stop_events.get(task_id)
    if not ev:
        return
    ev.set()
    update_task_status(task_id, 'stopped')
    _emit_live('system', f'任务 #{task_id} 已发出停止信号')
