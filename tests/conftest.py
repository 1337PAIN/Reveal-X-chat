"""Shared fixtures: every test runs against an isolated database and share store."""

import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The background retention sweeper would otherwise start on the first request
# and sweep whichever temporary database a later test is using.
# tests/test_sweeper.py starts its own when it needs one.
os.environ.setdefault('REVEALX_SWEEPER', 'off')

# With DATABASE_URL set (the PostgreSQL CI job) every test shares one real
# database, so the per-test temporary SQLite file no longer isolates anything.
# Instead every table but schema_version is emptied before and after each test.
#
# The first truncation has to happen here, before `from app import ...`:
# importing the app constructs the ChatRoom, which runs init_db, and init_db
# refuses to start when it finds encrypted TOTP secrets it cannot read -- which
# is exactly what a previous run's test_totp_at_rest leaves behind.
USING_POSTGRES = bool(os.environ.get('DATABASE_URL'))


def _truncate_postgres():
    import psycopg2
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = current_schema() AND tablename <> 'schema_version'"
            )
            tables = [row[0] for row in cur.fetchall()]
            if tables:
                cur.execute('TRUNCATE {} RESTART IDENTITY CASCADE'.format(
                    ', '.join('"{}"'.format(name) for name in tables)))
    finally:
        conn.close()


if USING_POSTGRES:
    _truncate_postgres()

from app import app as flask_app  # noqa: E402
from app.models import chat_room  # noqa: E402


@pytest.fixture(autouse=True)
def _empty_postgres():
    """PostgreSQL only: no rows leak from one test into the next."""
    if not USING_POSTGRES:
        yield
        return
    _truncate_postgres()
    yield
    _truncate_postgres()


@pytest.fixture(autouse=True)
def _fresh_send_buckets():
    """The send limiter is per process; start every test with full buckets."""
    from app import routes
    routes.SEND_BUCKETS.clear()
    yield
    routes.SEND_BUCKETS.clear()


@pytest.fixture
def store(tmp_path):
    """Point the singleton ChatRoom at a throwaway database and share folder."""
    original_db = chat_room.db_path
    original_shares = chat_room.shares_folder
    original_config_shares = flask_app.config['SHARES_FOLDER']

    shares = tmp_path / 'shares'
    shares.mkdir()

    chat_room.db_path = str(tmp_path / 'test.db')
    chat_room.shares_folder = str(shares)
    flask_app.config['SHARES_FOLDER'] = str(shares)
    chat_room.sessions = {}
    chat_room.user_sessions = {}
    chat_room.init_db()

    yield chat_room

    chat_room.db_path = original_db
    chat_room.shares_folder = original_shares
    flask_app.config['SHARES_FOLDER'] = original_config_shares
    chat_room.sessions = {}
    chat_room.user_sessions = {}


@pytest.fixture
def users(store):
    """Two registered accounts."""
    alice, error = store.create_user('alice', 'correct-horse-1')
    assert error is None
    bob, error = store.create_user('bob', 'correct-horse-2')
    assert error is None
    return alice, bob


@pytest.fixture
def client(store):
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as test_client:
        yield test_client


@pytest.fixture
def sample_image():
    """A deterministic gradient with some structure, as a grayscale array."""
    base = np.tile(np.arange(96, dtype=np.uint8), (96, 1))
    base[20:40, 20:40] = 240
    base[60:80, 10:70] = 15
    return base


@pytest.fixture
def random_share():
    """A share-shaped block of uniform random bytes."""
    return np.frombuffer(os.urandom(96 * 96), dtype=np.uint8).reshape((96, 96))


@pytest.fixture
def seeded_share():
    """A fixed share, for assertions on a single ML verdict.

    The classifier misreads a small fraction of genuine random shares, so tests
    that assert one verdict use a fixed input. The rate itself is asserted
    separately in test_tamper_detector.py.
    """
    return np.random.default_rng(20260822).integers(0, 256, size=(96, 96), dtype=np.uint8)
