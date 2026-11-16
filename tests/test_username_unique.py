"""One account per username, whatever the capitals, and sign-up says so.

Two things were wrong. Signing up as "Alice" when "alice" existed created a
second account: the UNIQUE constraint compares exactly, while adding a
contact, renaming and the admin tools all compare without case, so the two
were indistinguishable everywhere but the login box. And a name that was
taken exactly got "Request submitted" while nothing was created -- kept
vague on purpose, so the form could not be used to learn which names exist.

Usernames are now unique without regard to case (create_user, and a unique
index on LOWER(username), migration 12), sign-in accepts any capitals, and
sign-up answers honestly: "That username is already taken". The form also
asks `check_username` as you type. Since contacts are added by username, a
real name was already discoverable; the check is rate limited per address.
"""

import pytest

from app import app as flask_app, socketio
from app import migrations
from app.handlers import common

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


@pytest.fixture(autouse=True)
def _fresh():
    _received.clear()
    common.REGISTER_RATE.clear() if hasattr(common.REGISTER_RATE, 'clear') else None
    common.USERNAME_CHECK_RATE.clear() if hasattr(common.USERNAME_CHECK_RATE, 'clear') else None


def test_a_different_case_is_the_same_username(store):
    store.create_user('alice', 'correct-horse-1')
    user, error = store.create_user('ALICE', 'correct-horse-2')
    assert user is None and error == 'Username already exists'


def test_the_database_refuses_a_case_duplicate_too(store):
    """Migration 12: a unique index on LOWER(username), so a future path that
    skips create_user still cannot make one."""
    store.create_user('alice', 'correct-horse-1')
    with pytest.raises(Exception):
        with store._connect() as db:
            db.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES ('x', 'Alice', 'h', 'now')")


def test_the_migration_survives_existing_case_duplicates(store):
    """A database that already holds 'bob' and 'Bob' must still start: the
    index is skipped and the duplicates logged for an admin to resolve."""
    with store._connect() as db:
        db.execute("DROP INDEX IF EXISTS idx_users_username_lower")
        db.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('b1', 'bob', 'h', 'now')")
        db.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('b2', 'Bob', 'h', 'now')")
        migrations.m012_username_case(store, db)
        assert migrations.case_duplicate_usernames(db) == ['bob']


def test_sign_in_accepts_any_capitals(store):
    store.create_user('alice', 'correct-horse-1')
    user, error = store.authenticate('Alice', 'correct-horse-1')
    assert error is None and user['username'] == 'alice'


def test_signing_up_with_a_taken_name_says_so(store):
    store.create_user('taken', 'correct-horse-1')
    for name in ('taken', 'TAKEN'):
        sock = socketio.test_client(flask_app)
        sock.emit('register', {'username': name, 'password': 'different-pass-9'})
        assert events(sock, 'registration_submitted') == []
        assert events(sock, 'auth_error')[-1]['args'][0]['error'] == common.USERNAME_TAKEN
        sock.disconnect()


def check(sock, name):
    sock.emit('check_username', {'username': name})
    return events(sock, 'username_status')[-1]['args'][0]


def test_the_form_can_ask_before_submitting(store):
    store.create_user('alice', 'correct-horse-1')
    pending, _ = store.create_user('waiting', 'correct-horse-2', account_state='pending')
    sock = socketio.test_client(flask_app)      # no session: sign-up comes first
    assert check(sock, 'newname') == {'username': 'newname', 'available': True}
    for name in ('alice', 'Alice', 'waiting'):
        status = check(sock, name)
        assert status['available'] is False and status['error'] == common.USERNAME_TAKEN
    bad = check(sock, 'a b')
    assert bad['available'] is False and 'letters' in bad['error']
    sock.disconnect()


def test_the_check_is_rate_limited(store):
    sock = socketio.test_client(flask_app)
    for i in range(common.USERNAME_CHECK_MAX + 2):
        status = check(sock, f'name{i}')
    assert status['available'] is False and 'Too many' in status['error']
    sock.disconnect()
