"""Reconnect and resume, and what a socket without a session is told.

Sessions live on the socket. A dropped connection -- a phone changing
networks, a laptop waking, a proxy timing out -- came back as a new socket
with no session, and every handler that needs one returned *silently*. The
page looked signed in and did nothing: messages went nowhere, nothing said
why, and the only fix was a reload and a full sign-in, second factor
included.

Two things change. Sign-in issues a resume token (256 bits, held only in the
page's memory, stored server-side only as a SHA-256), which a reconnecting
socket presents to get its session back without a second TOTP prompt, within
12 hours and until anything invalidates it. And every handler that needs a
session now says so with `auth_required` instead of returning silently.
"""

import hashlib
from datetime import datetime, timedelta

import pyotp
import pytest

from app import app as flask_app, socketio
from app import routes
from app.auth.totp import generate_totp_secret
from app.auth import totp as totp_module

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


@pytest.fixture(autouse=True)
def _clean():
    _received.clear()
    routes.FAILED_LOGINS.clear()
    totp_module.reset_replay_cache()
    yield
    _received.clear()
    routes.FAILED_LOGINS.clear()


def sign_in(store, username='alice', password='correct-horse-1', create=True):
    if create:
        store.create_user(username, password)
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': username, 'password': password})
    success = events(sock, 'auth_success')
    return sock, (success[0]['args'][0] if success else None)


def resume(token):
    sock = socketio.test_client(flask_app)
    sock.emit('resume_session', {'token': token})
    return sock


def resumed(sock):
    return bool(events(sock, 'auth_success'))


# ----------------------------------------------------------------------
# The token
# ----------------------------------------------------------------------

def test_sign_in_issues_a_256_bit_resume_token(store):
    sock, payload = sign_in(store)
    token = payload['resume_token']
    # token_urlsafe(32): 32 random bytes, 43 base64url characters.
    assert len(token) >= 43
    sock.disconnect()


def test_only_the_hash_is_stored(store):
    sock, payload = sign_in(store)
    token = payload['resume_token']
    with store._connect() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM resume_tokens").fetchall()]
    assert len(rows) == 1
    flat = repr(rows)
    assert token not in flat, 'the bearer token itself was written to the database'
    assert hashlib.sha256(token.encode()).hexdigest() in flat
    sock.disconnect()


def test_a_reconnected_socket_resumes_its_session(store, users):
    alice, bob = users
    first, payload = sign_in(store, 'alice', 'correct-horse-1', create=False)
    first.disconnect()

    again = resume(payload['resume_token'])
    assert resumed(again)
    again.emit('send_message', {'recipient_id': bob['id'], 'message': 'back again'})
    assert store.get_conversation(alice['id'], bob['id'])[-1]['content'] == 'back again'
    again.disconnect()


def test_resuming_skips_the_second_factor_within_the_tokens_life(store):
    """That is the point of the token: a reconnect is not a new sign-in."""
    store.create_user('alice', 'correct-horse-1')
    user = next(a for a in store.list_accounts() if a['username'] == 'alice')
    secret = generate_totp_secret()
    store.set_totp_secret(user['id'], secret)

    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    assert events(sock, 'totp_required')
    sock.emit('verify_totp', {'code': pyotp.TOTP(secret).now()})
    token = events(sock, 'auth_success')[0]['args'][0]['resume_token']
    sock.disconnect()

    again = resume(token)
    assert resumed(again)
    assert not events(again, 'totp_required')
    again.disconnect()


def test_no_token_is_issued_before_the_second_factor(store):
    store.create_user('alice', 'correct-horse-1')
    user = next(a for a in store.list_accounts() if a['username'] == 'alice')
    store.set_totp_secret(user['id'], generate_totp_secret())
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    for item in _received.get(sock, []) + sock.get_received():
        assert 'resume_token' not in repr(item['args'])
    sock.disconnect()


# ----------------------------------------------------------------------
# Revocation and expiry
# ----------------------------------------------------------------------

def test_a_wrong_token_is_refused(store):
    sock = resume('not-a-real-token')
    assert not resumed(sock)
    assert events(sock, 'auth_required')
    sock.disconnect()


def test_an_expired_token_is_refused(store):
    sock, payload = sign_in(store)
    sock.disconnect()
    past = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
    with store._connect() as db:
        db.execute("UPDATE resume_tokens SET expires_at = ?", (past,))
    again = resume(payload['resume_token'])
    assert not resumed(again)
    assert events(again, 'auth_required')
    again.disconnect()


def test_tokens_expire_after_twelve_hours(store):
    sock, payload = sign_in(store)
    with store._connect() as db:
        # By name: sqlite3.Row and psycopg2's RealDictCursor rows both allow it,
        # and only SQLite's allows an index.
        expires = db.execute("SELECT expires_at FROM resume_tokens").fetchone()['expires_at']
    lifetime = datetime.fromisoformat(expires) - datetime.utcnow()
    assert timedelta(hours=11, minutes=59) < lifetime <= timedelta(hours=12)
    sock.disconnect()


def test_logout_revokes_the_token(store):
    sock, payload = sign_in(store)
    sock.emit('logout')
    sock.disconnect()
    again = resume(payload['resume_token'])
    assert not resumed(again)
    again.disconnect()


def _admin(store):
    store.create_user('root', 'correct-horse-9')
    root = next(a for a in store.list_accounts() if a['username'] == 'root')
    store.set_role(root['id'], 'admin')
    sock, _ = sign_in(store, 'root', 'correct-horse-9', create=False)
    return sock


@pytest.mark.parametrize('action', [
    lambda store, uid, admin: store.set_account_state(uid, 'disabled'),
    lambda store, uid, admin: store.set_role(uid, 'admin'),
    lambda store, uid, admin: store.set_password(uid, 'a-brand-new-pass-1'),
    lambda store, uid, admin: store.purge_user(uid),
    lambda store, uid, admin: admin.emit('admin_set_state', {'user_id': uid, 'state': 'disabled'}),
    lambda store, uid, admin: admin.emit('admin_reset_password',
                                         {'user_id': uid, 'password': 'a-brand-new-pass-1'}),
], ids=['disable', 'role-change', 'password-reset', 'deletion',
        'admin-disable-event', 'admin-reset-event'])
def test_account_changes_revoke_outstanding_tokens(store, action):
    sock, payload = sign_in(store)
    uid = payload['user']['id']
    sock.disconnect()
    admin = _admin(store)
    action(store, uid, admin)
    admin.disconnect()

    again = resume(payload['resume_token'])
    assert not resumed(again)
    again.disconnect()


def test_a_disabled_account_cannot_resume_even_with_a_live_token(store):
    """Belt and braces: the state is checked on redemption as well."""
    sock, payload = sign_in(store)
    uid = payload['user']['id']
    sock.disconnect()
    with store._connect() as db:
        db.execute("UPDATE users SET account_state = 'disabled' WHERE id = ?", (uid,))
    again = resume(payload['resume_token'])
    assert not resumed(again)
    again.disconnect()


# ----------------------------------------------------------------------
# auth_required instead of silence
# ----------------------------------------------------------------------

def test_a_send_without_a_session_yields_auth_required(store, users):
    alice, bob = users
    sock = socketio.test_client(flask_app)
    sock.emit('send_message', {'recipient_id': bob['id'], 'message': 'into the void'})
    assert events(sock, 'auth_required')
    assert store.get_conversation(alice['id'], bob['id']) == []
    sock.disconnect()


def _registered_events():
    return sorted(e for e in socketio.server.handlers['/'] if e not in ('connect', 'disconnect'))


@pytest.mark.parametrize('event', _registered_events())
def test_every_session_handler_says_so_when_there_is_none(store, event):
    """Enumerated from the server's own handler table, so a handler added
    later is covered without anyone remembering to list it."""
    if event in routes.PRE_AUTH_EVENTS:
        pytest.skip('reachable before sign-in by design')
    sock = socketio.test_client(flask_app)
    sock.emit(event, {})
    assert events(sock, 'auth_required'), f'{event} returned silently'
    sock.disconnect()


def test_the_pre_auth_list_is_short_and_deliberate():
    assert routes.PRE_AUTH_EVENTS == {
        'register', 'login', 'firebase_login', 'verify_totp', 'resume_session', 'logout',
    }


# ----------------------------------------------------------------------
# The client half
# ----------------------------------------------------------------------

def _chat_js():
    from pathlib import Path
    from chat_source import chat_source
    return chat_source()


def test_the_client_keeps_the_token_in_memory_only():
    import re
    chat = _chat_js()
    for call in re.findall(r'(?:local|session)Storage\.setItem\(([^;]*)', chat):
        assert 'resume' not in call.lower(), f'resume token written to web storage: {call}'
    assert 'state.resumeToken = data.resume_token' in chat


def test_the_client_resumes_on_reconnect_and_signs_in_on_failure():
    chat = _chat_js()
    connect = chat[chat.index("socket.on('connect'"):]
    connect = connect[:connect.index('\n});')]
    assert "socket.emit('resume_session'" in connect
    required = chat[chat.index("socket.on('auth_required'"):]
    required = required[:required.index('\n});')]
    assert "socket.emit('resume_session'" in required
    assert 'showSignIn(' in required
    sign_in = chat[chat.index('function showSignIn('):]
    sign_in = sign_in[:sign_in.index('\n}')]
    assert "authModal.style.display = ''" in sign_in
    assert 'state.resumeToken = null' in sign_in
