"""Server-side login throttling and the administrative audit trail.

The client shows an attempts counter, but it lives in the browser. An attacker
scripting the socket never sees it, so these tests drive the socket directly --
which is how the gap was found in the first place.
"""

import pytest

from app import app as flask_app, socketio
from app import routes

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def errors(client):
    return [e['args'][0]['error'] for e in events(client, 'auth_error')]


@pytest.fixture(autouse=True)
def _clean():
    _received.clear()
    routes.FAILED_LOGINS.clear()
    yield
    _received.clear()
    routes.FAILED_LOGINS.clear()


def hammer(sock, username, attempts):
    for i in range(attempts):
        sock.emit('login', {'username': username, 'password': f'wrong-guess-{i}'})


# ----------------------------------------------------------------------
# Throttling
# ----------------------------------------------------------------------

def test_repeated_failures_are_eventually_refused(store):
    store.create_user('victim', 'the-real-password-1')
    sock = socketio.test_client(flask_app)

    hammer(sock, 'victim', 40)

    assert any('Too many failed attempts' in e for e in errors(sock))
    sock.disconnect()


def test_the_correct_password_is_refused_while_locked_out(store):
    """Otherwise the limit only slows an attacker down and never stops them."""
    store.create_user('victim', 'the-real-password-1')
    sock = socketio.test_client(flask_app)

    hammer(sock, 'victim', routes.LOGIN_MAX_ATTEMPTS + 2)
    sock.emit('login', {'username': 'victim', 'password': 'the-real-password-1'})

    assert events(sock, 'auth_success') == []
    sock.disconnect()


def test_a_successful_sign_in_clears_the_counter(store):
    """A few typos before getting it right must not leave a lockout behind."""
    store.create_user('victim', 'the-real-password-1')
    sock = socketio.test_client(flask_app)

    hammer(sock, 'victim', routes.LOGIN_MAX_ATTEMPTS - 1)
    sock.emit('login', {'username': 'victim', 'password': 'the-real-password-1'})
    assert events(sock, 'auth_success')

    assert routes._login_blocked('victim', '127.0.0.1') == 0
    sock.disconnect()


def test_locking_one_account_does_not_lock_an_unrelated_one(store):
    """Per-account throttling must not become a way to lock out other people."""
    store.create_user('victim', 'the-real-password-1')
    store.create_user('bystander', 'another-password-2')

    attacker = socketio.test_client(flask_app)
    hammer(attacker, 'victim', routes.LOGIN_MAX_ATTEMPTS + 2)
    attacker.disconnect()

    other = socketio.test_client(flask_app)
    other.emit('login', {'username': 'bystander', 'password': 'another-password-2'})
    assert events(other, 'auth_success')
    other.disconnect()


def test_a_nonexistent_account_is_throttled_the_same_way(store):
    """If only real accounts were throttled, the difference would reveal which
    usernames exist."""
    sock = socketio.test_client(flask_app)
    hammer(sock, 'no-such-person', 40)

    assert any('Too many failed attempts' in e for e in errors(sock))
    sock.disconnect()


def test_spraying_many_accounts_is_capped_by_the_address_bucket(store):
    """Per-account limits alone are trivially sidestepped by trying one
    password against a hundred accounts instead."""
    for i in range(30):
        store.create_user(f'user_number_{i}', f'password-for-{i}-x')

    sock = socketio.test_client(flask_app)
    for i in range(30):
        sock.emit('login', {'username': f'user_number_{i}', 'password': 'Spring2026!'})

    assert any('Too many failed attempts' in e for e in errors(sock)), \
        'spraying across accounts was never throttled'
    sock.disconnect()


def test_old_failures_fall_out_of_the_window(store):
    """The window is a rolling one, not a permanent ban."""
    import time
    store.create_user('victim', 'the-real-password-1')
    sock = socketio.test_client(flask_app)
    hammer(sock, 'victim', routes.LOGIN_MAX_ATTEMPTS + 2)

    # Age every recorded failure past the window.
    stale = time.time() - routes.LOGIN_WINDOW_SECONDS - 1
    for key in routes.FAILED_LOGINS:
        routes.FAILED_LOGINS[key] = [stale] * len(routes.FAILED_LOGINS[key])

    assert routes._login_blocked('victim', '127.0.0.1') == 0
    sock.disconnect()


def test_the_failure_table_does_not_grow_without_bound(store):
    """It is only ever appended to, so it needs pruning or it is a slow leak."""
    import time
    stale = time.time() - routes.LOGIN_WINDOW_SECONDS - 1
    for i in range(2100):
        routes.FAILED_LOGINS[('account', f'ghost{i}')] = [stale]

    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'someone', 'password': 'wrong-password-1'})

    assert len(routes.FAILED_LOGINS) < 2100
    sock.disconnect()


# ----------------------------------------------------------------------
# Audit trail
# ----------------------------------------------------------------------

def admin_socket(store):
    store.create_user('root', 'correct-horse-admin', role='superadmin')
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'root', 'password': 'correct-horse-admin'})
    assert events(sock, 'auth_success')
    return sock


@pytest.mark.parametrize('action,event,payload_key', [
    ('set_state', 'admin_set_state', 'state'),
    ('delete_account', 'admin_delete_account', None),
    ('reset_password', 'admin_reset_password', 'password'),
])
def test_destructive_admin_actions_are_recorded(store, capsys, action, event, payload_key):
    """Deleting an account or resetting a password previously left no trace of
    who did it."""
    sock = admin_socket(store)
    target, _ = store.create_user('subject', 'correct-horse-1')

    payload = {'user_id': target['id']}
    if payload_key == 'state':
        payload['state'] = 'disabled'
    elif payload_key == 'password':
        payload['password'] = 'a-new-password-1'
    sock.emit(event, payload)

    logged = capsys.readouterr().out
    assert '[audit]' in logged, f'{action} was not audited'
    assert f'action={action}' in logged
    assert 'actor=root' in logged
    sock.disconnect()


def test_the_audit_line_never_contains_the_new_password(store, capsys):
    sock = admin_socket(store)
    target, _ = store.create_user('subject', 'correct-horse-1')

    sock.emit('admin_reset_password',
              {'user_id': target['id'], 'password': 'sup3r-secret-value'})

    assert 'sup3r-secret-value' not in capsys.readouterr().out
    sock.disconnect()


def test_a_refused_action_is_not_audited_as_if_it_happened(store, capsys):
    """An audit trail that records attempts as successes is worse than none."""
    store.create_user('plain', 'correct-horse-1')
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'plain', 'password': 'correct-horse-1'})
    assert events(sock, 'auth_success')
    capsys.readouterr()

    victim, _ = store.create_user('subject', 'correct-horse-2')
    sock.emit('admin_delete_account', {'user_id': victim['id']})

    assert 'action=delete_account' not in capsys.readouterr().out
    assert store.get_user(victim['id']) is not None
    sock.disconnect()
