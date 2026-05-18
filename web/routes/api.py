import csv
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flask import Blueprint, jsonify, request

from api.answer import Tiku
from api.base import Account, Chaoxing
from web import models
from web.models import (
    add_operation_log,
    clear_all_interventions,
    clear_dashboard_logs,
    clear_dashboard_progress,
    clear_intervention,
    count_tasks_completed_today,
    get_intervention_needed,
    get_operation_logs,
)
from web.system_metrics import get_system_metrics
from web.tasks import clear_queue, delete_task, get_scheduler_stats, set_scheduler_paused, start_task, stop_task
from web.tiku_config import build_effective_tiku_config

api_bp = Blueprint('api', __name__)


def _build_account(user, cookies_data=None):
    return Account(
        user['username'],
        user.get('password', ''),
        cookies_data=user.get('cookies_data') if cookies_data is None else cookies_data,
        user_agent=user.get('user_agent') or None,
        use_cookie_file=False,
    )


def _init_tiku_for_user(user):
    tc = build_effective_tiku_config(user.get('tiku_config') or {}, models.get_settings())
    multi_model = tc.get('multi_model', 'false') in ('true', 'True', '1', 'yes')
    if multi_model:
        from api.answer import EnsembleTiku

        tiku = EnsembleTiku()
        tiku.config_set(tc)
        tiku.init_tiku()
    else:
        tiku = Tiku()
        tiku.config_set(tc)
        tiku = tiku.get_tiku_from_config()
        tiku.init_tiku()
    return tiku, tc


def _make_cx(user, include_tiku=True, login=True, cookies_data=None):
    import warnings

    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
    tiku = None
    query_delay = 0
    if include_tiku:
        tiku, tc = _init_tiku_for_user(user)
        query_delay = float(tc.get('delay', 0))
    account = _build_account(user, cookies_data=cookies_data)
    cx = Chaoxing(account=account, tiku=tiku, query_delay=query_delay)

    if not login:
        return cx

    login_with_cookies = bool(account.cookies_data or user.get('use_cookies'))
    result = cx.login(login_with_cookies=login_with_cookies)
    if not result['status']:
        raise Exception(result['msg'])
    if getattr(cx.account, 'cookies_data', None):
        models.update_user(user['id'], {'cookies_data': cx.account.cookies_data})
    return cx


def _fetch_courses(cx, use_cookies=False):
    """获取课程列表，失败时自动重试账号密码登录"""
    courses = cx.get_course_list()
    if not courses and use_cookies:
        result = cx.login(login_with_cookies=False)
        if result['status']:
            courses = cx.get_course_list()
    return courses


def _course_progress_worker(user, cookies_data, course):
    course_copy = dict(course)
    try:
        cx = _make_cx(user, include_tiku=False, login=False, cookies_data=cookies_data)
        points = cx.get_course_point(course_copy['courseId'], course_copy.get('clazzId', ''), course_copy.get('cpi', ''))
        course_copy['total_points'] = len(points.get('points', []))
        course_copy['done_points'] = sum(1 for point in points.get('points', []) if point.get('has_finished'))
    except Exception:
        course_copy['total_points'] = 0
        course_copy['done_points'] = 0
    return course_copy


def _enrich_courses_with_progress(user, courses, cookies_data):
    if not courses:
        return []

    settings = models.get_settings()
    max_workers = min(len(courses), max(1, int(settings.get('course_progress_workers', 3))))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(lambda course: _course_progress_worker(user, cookies_data, course), courses))


@api_bp.route('/api/dashboard')
def dashboard():
    settings = models.get_settings()
    scheduler = get_scheduler_stats()
    running_tasks = scheduler['active']
    return jsonify(
        total_users=len(models.get_users()),
        running_tasks=running_tasks,
        pending_tasks=scheduler['pending'],
        today_done=count_tasks_completed_today(),
        timezone=settings.get('timezone'),
        show_system_metrics=settings.get('show_system_metrics', True),
        auto_theme_follow_system=settings.get('auto_theme_follow_system', True),
        list_show_remark=settings.get('list_show_remark', False),
        scheduler=scheduler,
        recent_logs=models.get_recent_logs(50),
        progress=models.get_progress(),
        system_metrics=get_system_metrics() if settings.get('show_system_metrics', True) else None,
    )


@api_bp.route('/api/system/metrics')
def system_metrics():
    settings = models.get_settings()
    if not settings.get('show_system_metrics', True):
        return jsonify(enabled=False)
    payload = get_system_metrics()
    payload['enabled'] = True
    return jsonify(payload)


@api_bp.route('/api/users', methods=['GET'])
def list_users():
    return jsonify(models.get_users())


@api_bp.route('/api/users', methods=['POST'])
def add_user():
    data = request.json or {}
    if not data.get('username'):
        return jsonify(error='username required'), 400
    try:
        models.create_user(data)
        add_operation_log('user', f"添加用户: {data.get('username','')}")
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(error=str(e)), 400


@api_bp.route('/api/users/<int:uid>', methods=['PUT'])
def update_user(uid):
    models.update_user(uid, request.json or {})
    return jsonify(ok=True)


@api_bp.route('/api/users/<int:uid>', methods=['DELETE'])
def delete_user(uid):
    models.delete_user(uid)
    add_operation_log('user', f"删除用户 ID:{uid}")
    return jsonify(ok=True)


@api_bp.route('/api/users/import', methods=['POST'])
def import_users():
    file = request.files.get('file')
    if not file:
        return jsonify(error='no file'), 400
    text = file.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    created, errors = 0, []
    for row in reader:
        try:
            models.create_user({'username': row.get('username', '').strip(), 'password': row.get('password', '').strip()})
            created += 1
        except Exception as e:
            errors.append(str(e))
    add_operation_log('user', f"批量导入 {created} 个用户")
    return jsonify(created=created, errors=errors)


@api_bp.route('/api/users/<int:uid>/courses')
def get_courses(uid):
    user = models.get_user(uid)
    if not user:
        return jsonify(error='not found'), 404
    try:
        cx = _make_cx(user, include_tiku=False)
        courses = _fetch_courses(cx, bool(user.get('use_cookies')))
        if request.args.get('progress', '0') == '1':
            courses = _enrich_courses_with_progress(user, courses, getattr(cx.account, 'cookies_data', None))
        add_operation_log('api', f'用户 {user["username"]} 获取了 {len(courses)} 门课程')
        return jsonify(courses)
    except Exception as e:
        return jsonify(error=str(e)), 500


@api_bp.route('/api/users/<int:uid>/courses/progress', methods=['POST'])
def get_courses_progress(uid):
    user = models.get_user(uid)
    if not user:
        return jsonify(error='not found'), 404

    payload = request.json or {}
    courses = payload.get('courses') or []
    if not isinstance(courses, list):
        return jsonify(error='courses must be a list'), 400

    try:
        cookies_data = user.get('cookies_data') or None
        cx = None
        if not cookies_data:
            cx = _make_cx(user, include_tiku=False)
            cookies_data = getattr(cx.account, 'cookies_data', None)
        if not courses:
            if cx is None:
                cx = _make_cx(user, include_tiku=False)
            courses = _fetch_courses(cx, bool(user.get('use_cookies')))
        courses = _enrich_courses_with_progress(user, courses, cookies_data)
        return jsonify(courses)
    except Exception as e:
        return jsonify(error=str(e)), 500


@api_bp.route('/api/users/<int:uid>/courses/<course_id>/points')
def get_points(uid, course_id):
    user = models.get_user(uid)
    if not user:
        return jsonify(error='not found'), 404
    try:
        if user.get('cookies_data'):
            cx = _make_cx(user, include_tiku=False, login=False)
        else:
            cx = _make_cx(user, include_tiku=False)
        return jsonify(cx.get_course_point(course_id, request.args.get('clazzId', ''), request.args.get('cpi', '')))
    except Exception as e:
        return jsonify(error=str(e)), 500


@api_bp.route('/api/study/start', methods=['POST'])
def study_start():
    data = request.json or {}
    uid = data.get('user_id')
    courses = data.get('courses', [])
    if not uid or not courses:
        return jsonify(error='user_id and courses required'), 400
    chapter_ids = data.get('chapter_ids')
    ids = [start_task(uid, c['courseId'], c.get('title', c['courseId']), chapter_ids) for c in courses]
    add_operation_log('study', f"添加 {len(ids)} 个任务到队列")
    return jsonify(task_ids=ids)


@api_bp.route('/api/study/stop/<int:task_id>', methods=['POST'])
def study_stop(task_id):
    stop_task(task_id)
    return jsonify(ok=True)


@api_bp.route('/api/study/task/<int:task_id>', methods=['DELETE'])
def study_delete(task_id):
    delete_task(task_id)
    add_operation_log('study', f"删除任务 #{task_id}")
    return jsonify(ok=True)


@api_bp.route('/api/study/tasks')
def study_tasks():
    return jsonify(models.get_tasks())


@api_bp.route('/api/study/queue/status')
def study_queue_status():
    return jsonify(get_scheduler_stats())


@api_bp.route('/api/study/queue/pause', methods=['POST'])
def study_queue_pause():
    set_scheduler_paused(True)
    add_operation_log('study', '任务队列已暂停')
    return jsonify(ok=True)


@api_bp.route('/api/study/queue/resume', methods=['POST'])
def study_queue_resume():
    set_scheduler_paused(False)
    add_operation_log('study', '任务队列已恢复')
    return jsonify(ok=True)


@api_bp.route('/api/study/queue/clear', methods=['POST'])
def study_queue_clear():
    clear_queue()
    add_operation_log('study', '任务队列已清空')
    return jsonify(ok=True)


@api_bp.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(models.get_settings())


@api_bp.route('/api/settings', methods=['PUT'])
def put_settings():
    models.set_settings(request.json or {})
    return jsonify(ok=True)


@api_bp.route('/api/settings/sync/<int:uid>', methods=['POST'])
def sync_settings(uid):
    settings = models.get_settings()
    user = models.get_user(uid)
    if not user:
        return jsonify(error='not found'), 404
    payload = request.json or {}
    scope = payload.get('scope', 'tiku')
    global_tc = dict(settings.get('tiku_config') or {})
    user_tc = dict(user.get('tiku_config') or {})

    if scope == 'all':
        user_tc.update(global_tc)
    elif scope == 'ai':
        for key in ('endpoint', 'key', 'model'):
            user_tc[key] = global_tc.get(key, '')
    else:
        for key, value in global_tc.items():
            if key in ('endpoint', 'key', 'model'):
                continue
            user_tc[key] = value

    models.update_user(uid, {'tiku_config': user_tc})
    return jsonify(ok=True)


@api_bp.route('/api/logs')
def get_logs():
    return jsonify(get_operation_logs(int(request.args.get('limit', 200))))


@api_bp.route('/api/intervention')
def get_intervention():
    return jsonify(get_intervention_needed())


@api_bp.route('/api/intervention/<int:log_id>', methods=['DELETE'])
def dismiss_intervention(log_id):
    clear_intervention(log_id)
    add_operation_log('intervention', f'清除了人工接管记录 #{log_id}')
    return jsonify(ok=True)


@api_bp.route('/api/intervention/clear', methods=['POST'])
def dismiss_all_interventions():
    clear_all_interventions()
    add_operation_log('intervention', '清空了全部人工接管记录')
    return jsonify(ok=True)


@api_bp.route('/api/dashboard/logs/clear', methods=['POST'])
def clear_dashboard_logs_route():
    clear_dashboard_logs()
    add_operation_log('dashboard', '清空了首页章节日志')
    return jsonify(ok=True)


@api_bp.route('/api/dashboard/progress/clear', methods=['POST'])
def clear_dashboard_progress_route():
    clear_dashboard_progress()
    add_operation_log('dashboard', '清空了首页学习进度')
    return jsonify(ok=True)
