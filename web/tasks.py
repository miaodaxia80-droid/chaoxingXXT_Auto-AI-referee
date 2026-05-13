import queue
import threading
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.base import Chaoxing, Account
from api.answer import Tiku
from api.exceptions import LoginError
from api.logger import logger
from web.models import create_task, update_task_status, add_chapter_log, get_user, add_operation_log

# task_id -> threading.Event (set to stop)
_stop_events: dict[int, threading.Event] = {}
# task_id -> list of SSE queues (for chapter progress)
_sse_queues: dict[int, list] = {}
# Global live log subscribers for web UI broadcast
_live_log_subscribers: list = []          # list of queue.Queue, one per SSE client
_live_log_lock = threading.Lock()
_live_log_history: list = []              # ring buffer of recent messages (max 200)

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

# Capture all loguru output to live log broadcast
def _loguru_sink(msg):
    """Push formatted loguru message to the live log broadcast."""
    payload = {
        'category': 'log',
        'message': str(msg).rstrip(),
        'ts': datetime.now().isoformat()
    }
    _broadcast_log(payload)

_loguru_sink_id = getattr(logger, '_web_sink_id', None)
if _loguru_sink_id is None:
    _loguru_sink_id = logger.add(_loguru_sink, level="DEBUG", format="{time:HH:mm:ss.SSS} | {message}")
    logger._web_sink_id = _loguru_sink_id

# Task queue for sequential processing
_task_queue: queue.Queue = queue.Queue()
_worker_running = False
_worker_lock = threading.Lock()

def subscribe_sse(task_id, q):
    _sse_queues.setdefault(task_id, []).append(q)

def unsubscribe_sse(task_id, q):
    try:
        _sse_queues.get(task_id, []).remove(q)
    except ValueError:
        pass

def _emit(task_id, chapter_title, result, message=''):
    add_chapter_log(task_id, chapter_title, result, message)
    payload = {'chapter_title': chapter_title, 'result': result, 'message': message, 'ts': datetime.now().isoformat()}
    for q in list(_sse_queues.get(task_id, [])):
        try:
            q.put_nowait(payload)
        except Exception:
            pass

def _emit_live(category, message):
    """推送实时日志到 web UI"""
    payload = {'category': category, 'message': message, 'ts': datetime.now().isoformat()}
    add_operation_log(category, message)
    _broadcast_log(payload)

def _build_chaoxing(user: dict) -> Chaoxing:
    tiku_conf = user.get('tiku_config') or {}
    tiku_conf.setdefault('true_list', '正确,对,√,是')
    tiku_conf.setdefault('false_list', '错误,错,×,否,不对,不正确')
    tiku_conf.setdefault('submit', 'false')
    tiku_conf.setdefault('cover_rate', '0.9')
    tiku_conf.setdefault('delay', '1.0')
    tiku_conf.setdefault('likeapi_search', 'false')
    tiku_conf.setdefault('likeapi_model', 'deepseek-v3')
    tiku_conf.setdefault('url', '')
    tiku_conf.setdefault('endpoint', '')
    tiku_conf.setdefault('key', '')
    tiku_conf.setdefault('model', '')
    tiku_conf.setdefault('http_proxy', '')
    tiku_conf.setdefault('min_interval_seconds', '3')
    tiku_conf.setdefault('siliconflow_key', '')
    tiku_conf.setdefault('siliconflow_model', 'deepseek-ai/DeepSeek-V3')
    tiku_conf.setdefault('siliconflow_endpoint', 'https://api.siliconflow.cn/v1/chat/completions')
    tiku_conf.setdefault('multi_model', 'false')
    tiku_conf.setdefault('models', '[]')
    tiku_conf.setdefault('search_enabled', 'false')
    tiku_conf.setdefault('search_max_results', '3')
    tiku_conf.setdefault('voting_strategy', 'referee')
    tiku_conf.setdefault('referee_model_index', '0')

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

        # 如果指定了章节，过滤 course points
        if chapter_ids:
            point_list = cx.get_course_point(course['courseId'], course['clazzId'], course['cpi'])
            all_points = point_list.get('points', [])
            selected = [p for p in all_points if p['id'] in chapter_ids]
            _emit_live('study', f'选择了 {len(selected)}/{len(all_points)} 个章节')
            # Monkey-patch get_course_point to return only selected chapters
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


def _task_worker():
    """单线程任务队列 worker，顺序处理学习任务"""
    global _worker_running
    _emit_live('system', '任务队列 worker 已启动')
    while True:
        try:
            task = _task_queue.get(timeout=5)
        except queue.Empty:
            # 检查是否还有待处理任务
            continue
        if task is None:  # shutdown signal
            _emit_live('system', '任务队列 worker 已停止')
            break
        task_id, user_id, course_id, course_title, chapter_ids = task
        _emit_live('system', f'队列处理: #{task_id} {course_title}')
        stop_event = threading.Event()
        _stop_events[task_id] = stop_event
        _run_task(task_id, user_id, course_id, course_title, stop_event, chapter_ids)
        _task_queue.task_done()
    _worker_running = False


def _ensure_worker():
    global _worker_running
    with _worker_lock:
        if not _worker_running:
            _worker_running = True  # set BEFORE starting thread to prevent race
            t = threading.Thread(target=_task_worker, daemon=True)
            t.start()


def start_task(user_id: int, course_id: str, course_title: str, chapter_ids: list = None) -> int:
    task_id = create_task(user_id, course_id, course_title)
    _ensure_worker()
    _task_queue.put((task_id, user_id, course_id, course_title, chapter_ids))
    _emit_live('system', f'任务已加入队列: #{task_id} {course_title}')
    return task_id


def stop_task(task_id: int):
    ev = _stop_events.get(task_id)
    if ev:
        ev.set()
        update_task_status(task_id, 'stopped')
        _emit_live('system', f'任务 #{task_id} 已发出停止信号')
