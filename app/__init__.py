"""
Reveal-X Application Initialization
"""

from flask import Flask
from flask_socketio import SocketIO
import os
import secrets

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('REVEAL_X_SECRET_KEY') or secrets.token_urlsafe(32)
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024
# Jinja caches compiled templates and, outside debug mode, never re-checks the
# file. Editing a template then reloading would keep serving the old page --
# which looks exactly like the edit having no effect. The cost is one stat()
# per render.
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SHARES_FOLDER'] = os.path.join(app.root_path, 'shares')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('REVEAL_X_COOKIE_SECURE', '').lower() == 'true'

# Socket.IO origins. When REVEAL_X_ALLOWED_ORIGINS is unset the argument is not
# passed at all, which makes engine.io fall back to same-origin only. That still
# covers LAN demos (the browser origin is the server it loaded from) without
# leaving the websocket open to every site on the internet.
allowed_origins = os.environ.get('REVEAL_X_ALLOWED_ORIGINS', '').strip()
app.config['ALLOWED_ORIGINS'] = [
    origin.strip()
    for origin in allowed_origins.split(',')
    if origin.strip()
] or None

# Ensure folders exist
os.makedirs(app.config['SHARES_FOLDER'], exist_ok=True)

socketio_options = {
    'async_mode': 'threading',
    'max_http_buffer_size': app.config['MAX_CONTENT_LENGTH'],
}
if app.config['ALLOWED_ORIGINS']:
    socketio_options['cors_allowed_origins'] = app.config['ALLOWED_ORIGINS']

socketio = SocketIO(app, **socketio_options)

# Import routes after app creation
from app import routes


def _bootstrap_admin():
    """Create or promote the administrator named by the environment.

    Registration is request-and-approve, so a deployment with no admin has no
    way to approve anyone -- it would be permanently locked. This runs on every
    start and is idempotent.

    The password is only used when the account does not exist yet; an existing
    admin's password is never overwritten from the environment. Nothing is
    printed except the username, so the password does not end up in logs or a
    screen-shared terminal.
    """
    from app.models import chat_room

    username = os.environ.get('REVEALX_ADMIN_USER', '').strip()
    password = os.environ.get('REVEALX_ADMIN_PASSWORD', '')

    if not username:
        if chat_room.count_admins() == 0:
            print('[admin] No administrator configured. Set REVEALX_ADMIN_USER and '
                  'REVEALX_ADMIN_PASSWORD, then restart. Until then, account '
                  'requests cannot be approved. See docs/ADMIN.md.')
        return

    user, created, error = chat_room.ensure_admin(username, password)
    if error:
        print(f'[admin] Could not set up administrator "{username}": {error}')
    elif created:
        print(f'[admin] Created administrator account "{username}".')
    else:
        print(f'[admin] Administrator "{username}" is active.')


try:
    from app.models import chat_room as _chat_room
    _chat_room.init_db()
    _bootstrap_admin()
except Exception as _exc:  # pragma: no cover - surfaced at startup
    print(f'[admin] Startup admin check failed: {_exc}')
