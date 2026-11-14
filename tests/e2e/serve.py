"""Run Reveal-X for the browser end-to-end tests.

    python tests/e2e/serve.py <data dir> <port>

The store is pointed at <data dir> before any account exists, so these tests
never touch app/data/reveal_x.db. Two active accounts are seeded; their
passwords are test values that exist only in this throwaway database.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2])

os.environ.setdefault('REVEALX_SWEEPER', 'off')
os.environ.setdefault('REVEAL_X_SECRET_KEY', 'e2e-only-secret-key-not-used-anywhere-else')
# Unset, the server would use a real PostgreSQL if this shell had one.
os.environ.pop('DATABASE_URL', None)

(DATA / 'shares').mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import app, socketio  # noqa: E402
from app.models import chat_room  # noqa: E402

chat_room.db_path = str(DATA / 'e2e.db')
chat_room.shares_folder = str(DATA / 'shares')
app.config['SHARES_FOLDER'] = str(DATA / 'shares')
chat_room.init_db()

ACCOUNTS = (('e2ealice', 'e2e-local-pass-a1'), ('e2ebob', 'e2e-local-pass-b2'))
existing = {u['username'] for u in chat_room.list_accounts()}
for name, password in ACCOUNTS:
    if name not in existing:
        chat_room.create_user(name, password)

socketio.run(app, host='127.0.0.1', port=PORT, allow_unsafe_werkzeug=True)
