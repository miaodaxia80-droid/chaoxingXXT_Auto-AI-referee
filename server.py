import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web import create_app
from web.models import reconcile_incomplete_tasks
from web.tasks import _ensure_scheduler

app = create_app(reconcile_tasks=False)

if __name__ == '__main__':
    reconcile_incomplete_tasks()
    _ensure_scheduler()
    app.run(host='0.0.0.0', port=5002, threaded=True)
