import csv
import io
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flask import Blueprint, request, jsonify
from web import models
from web.tasks import start_task, stop_task
from web.models import add_operation_log, get_operation_logs, get_intervention_needed

api_bp = Blueprint('api', __name__)

@api_bp.route('/api/dashboard')
def dashboard():
    tasks = models.get_tasks(20)
    running = sum(1 for t in tasks if t['status'] == 'running')
    return jsonify(
        total_users=len(models.get_users()),
        running_tasks=running,
        recent_logs=models.get_recent_logs(50),
        progress=models.get_progress(),
    )

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
    f = request.files.get('file')
    if not f:
        return jsonify(error='no file'), 400
    text = f.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    created, errors = 0, []
    for row in reader:
        try:
            models.create_user({'username': row.get('username','').strip(), 'password': row.get('password','').strip()})
            created += 1
        except Exception as e:
            errors.append(str(e))
    add_operation_log('user', f"批量导入 {created} 个用户")
    return jsonify(created=created, errors=errors)

def _make_cx(user):
    from api.base import Chaoxing, Account
    from api.answer import Tiku
    import warnings
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')

    tc = user.get('tiku_config') or {}
    # 补充判断题选项映射默认值，避免 KeyError
    tc.setdefault('true_list', '正确,对,√,是')
    tc.setdefault('false_list', '错误,错,×,否,不对,不正确')
    tc.setdefault('submit', 'false')
    tc.setdefault('cover_rate', '0.9')
    tc.setdefault('delay', '1.0')
    tc.setdefault('likeapi_search', 'false')
    tc.setdefault('likeapi_model', 'deepseek-v3')
    tc.setdefault('url', '')
    tc.setdefault('endpoint', '')
    tc.setdefault('key', '')
    tc.setdefault('model', '')
    tc.setdefault('http_proxy', '')
    tc.setdefault('min_interval_seconds', '3')
    tc.setdefault('siliconflow_key', '')
    tc.setdefault('siliconflow_model', 'deepseek-ai/DeepSeek-V3')
    tc.setdefault('siliconflow_endpoint', 'https://api.siliconflow.cn/v1/chat/completions')
    tc.setdefault('multi_model', 'false')
    tc.setdefault('models', '[]')
    tc.setdefault('search_enabled', 'false')
    tc.setdefault('search_max_results', '3')
    tc.setdefault('voting_strategy', 'referee')
    tc.setdefault('referee_model_index', '0')

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

    use_cookies = bool(user.get('use_cookies'))
    cx = Chaoxing(
        account=Account(
            user['username'],
            user.get('password',''),
            cookies_data=user.get('cookies_data') or "",
            user_agent=user.get('user_agent') or None,
            use_cookie_file=False,
        ),
        tiku=tiku,
    )

    r = cx.login(login_with_cookies=use_cookies)
    if not r['status']:
        raise Exception(r['msg'])
    if getattr(cx.account, 'cookies_data', None):
        models.update_user(user['id'], {'cookies_data': cx.account.cookies_data})
    return cx

def _fetch_courses(cx, use_cookies=False):
    """获取课程列表，失败时自动重试账号密码登录"""
    courses = cx.get_course_list()
    if not courses and use_cookies:
        r = cx.login(login_with_cookies=False)
        if r['status']:
            courses = cx.get_course_list()
    return courses

@api_bp.route('/api/users/<int:uid>/courses')
def get_courses(uid):
    user = models.get_user(uid)
    if not user:
        return jsonify(error='not found'), 404
    try:
        cx = _make_cx(user)
        courses = _fetch_courses(cx, bool(user.get('use_cookies')))
        fetch_progress = request.args.get('progress', '0') == '1'
        if fetch_progress and courses:
            def _fetch_one(c):
                try:
                    pts = cx.get_course_point(c['courseId'], c.get('clazzId',''), c.get('cpi',''))
                    c['total_points'] = len(pts.get('points',[]))
                    c['done_points'] = sum(1 for p in pts.get('points',[]) if p.get('has_finished'))
                except Exception:
                    c['total_points'] = 0
                    c['done_points'] = 0
                return c
            courses = [_fetch_one(course) for course in courses]
        add_operation_log('api', f'用户 {user["username"]} 获取了 {len(courses)} 门课程')
        return jsonify(courses)
    except Exception as e:
        return jsonify(error=str(e)), 500

@api_bp.route('/api/users/<int:uid>/courses/<course_id>/points')
def get_points(uid, course_id):
    user = models.get_user(uid)
    if not user:
        return jsonify(error='not found'), 404
    try:
        cx = _make_cx(user)
        return jsonify(cx.get_course_point(course_id, request.args.get('clazzId',''), request.args.get('cpi','')))
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

@api_bp.route('/api/study/tasks')
def study_tasks():
    return jsonify(models.get_tasks())

@api_bp.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(models.get_settings())

@api_bp.route('/api/settings', methods=['PUT'])
def put_settings():
    models.set_settings(request.json or {})
    return jsonify(ok=True)

@api_bp.route('/api/settings/sync/<int:uid>', methods=['POST'])
def sync_settings(uid):
    s = models.get_settings()
    if s.get('tiku_config'):
        models.update_user(uid, {'tiku_config': s['tiku_config']})
    return jsonify(ok=True)
# --- Logs ---
@api_bp.route('/api/logs')
def get_logs():
    return jsonify(get_operation_logs(int(request.args.get('limit', 200))))

# --- Intervention ---
@api_bp.route('/api/intervention')
def get_intervention():
    return jsonify(get_intervention_needed())
