import json
import time
from flask import Blueprint, Response, request
from web.models import get_chapter_logs, get_operation_logs_after, get_task

sse_bp = Blueprint('sse', __name__)

@sse_bp.route('/api/stream/<int:task_id>')
def stream(task_id):
    task = get_task(task_id)
    if not task:
        return Response('not found', status=404)

    def generate():
        last_id = 0
        heartbeat_at = time.monotonic()
        for log in get_chapter_logs(task_id):
            last_id = log['id']
            yield "data: " + json.dumps(log) + "\n\n"

        while True:
            current = get_task(task_id)
            new_logs = get_chapter_logs(task_id, after_id=last_id)
            for item in new_logs:
                last_id = item['id']
                yield "data: " + json.dumps(item) + "\n\n"

            if current and current['status'] in ('done', 'error', 'stopped') and not new_logs:
                yield "data: null\n\n"
                return

            if time.monotonic() - heartbeat_at >= 30:
                heartbeat_at = time.monotonic()
                yield ": keepalive\n\n"
            time.sleep(1)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@sse_bp.route('/api/log-stream')
def log_stream():
    try:
        last_id = int(request.args.get('after_id') or request.headers.get('Last-Event-ID') or 0)
    except (TypeError, ValueError):
        last_id = 0

    def generate():
        current_id = last_id
        heartbeat_at = time.monotonic()
        if current_id <= 0:
            for item in get_operation_logs_after():
                current_id = item['id']
                yield f"id: {current_id}\n"
                yield "data: " + json.dumps(item) + "\n\n"

        while True:
            items = get_operation_logs_after(current_id)
            for item in items:
                current_id = item['id']
                yield f"id: {current_id}\n"
                yield "data: " + json.dumps(item) + "\n\n"
            if time.monotonic() - heartbeat_at >= 30:
                heartbeat_at = time.monotonic()
                yield ": keepalive\n\n"
            time.sleep(1)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
