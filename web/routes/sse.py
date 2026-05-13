import queue
import json
from flask import Blueprint, Response
from web.models import get_chapter_logs, get_task
from web.tasks import subscribe_sse, unsubscribe_sse, subscribe_live_log, unsubscribe_live_log

sse_bp = Blueprint('sse', __name__)

@sse_bp.route('/api/stream/<int:task_id>')
def stream(task_id):
    task = get_task(task_id)
    if not task:
        return Response('not found', status=404)

    def generate():
        q = queue.Queue()
        subscribe_sse(task_id, q)
        for log in get_chapter_logs(task_id):
            yield "data: " + json.dumps(log) + "\n\n"
        if task['status'] in ('done', 'error', 'stopped'):
            yield "data: null\n\n"
            unsubscribe_sse(task_id, q)
            return
        try:
            while True:
                try:
                    item = q.get(timeout=30)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    yield "data: null\n\n"
                    break
                yield "data: " + json.dumps(item) + "\n\n"
        finally:
            unsubscribe_sse(task_id, q)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@sse_bp.route('/api/log-stream')
def log_stream():
    def generate():
        q = queue.Queue()
        history = subscribe_live_log(q)
        # send recent history first (no drain from shared queue)
        for item in history:
            yield "data: " + json.dumps(item) + "\n\n"
        try:
            while True:
                try:
                    item = q.get(timeout=30)
                    yield "data: " + json.dumps(item) + "\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe_live_log(q)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
