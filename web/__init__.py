from flask import Flask
from web.models import init_db, reconcile_incomplete_tasks

def create_app():
    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config['JSON_ENSURE_ASCII'] = False
    init_db()
    reconcile_incomplete_tasks()
    from web.routes.api import api_bp
    from web.routes.sse import sse_bp
    app.register_blueprint(api_bp)
    app.register_blueprint(sse_bp)

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def index(path):
        return app.send_static_file('index.html')

    return app
