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
    count = routes.LOGIN_IP_MAX_ATTEMPTS + 10
    for i in range(count):
        store.create_user(f'user_number_{i}', f'password-for-{i}-x')

    sock = socketio.test_client(flask_app)
    for i in range(count):
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


# ----------------------------------------------------------------------
# Only guesses count
#
# A refusal for account state happens *after* the password has already
# verified, so it is not evidence of guessing. Counting it locked legitimate
# users out: request an account, keep trying while you wait, and the moment an
# admin approved you the correct password was refused for 15 minutes.
# ----------------------------------------------------------------------

def test_waiting_for_approval_does_not_burn_the_throttle(store):
    sock = socketio.test_client(flask_app)
    sock.emit('register', {'username': 'newperson', 'password': 'their-password-1'})

    for _ in range(routes.LOGIN_MAX_ATTEMPTS * 2):
        sock.emit('login', {'username': 'newperson', 'password': 'their-password-1'})

    pending = [a for a in store.list_accounts() if a['username'] == 'newperson'][0]
    store.set_account_state(pending['id'], 'active')

    sock.emit('login', {'username': 'newperson', 'password': 'their-password-1'})
    assert events(sock, 'auth_success'), 'approved user was locked out by their own waiting'
    sock.disconnect()


def test_a_disabled_account_is_not_throttled_into_a_different_message(store):
    """The reason must stay honest however many times they try."""
    user, _ = store.create_user('gone', 'the-real-password-1')
    store.set_account_state(user['id'], 'disabled')

    sock = socketio.test_client(flask_app)
    for _ in range(routes.LOGIN_MAX_ATTEMPTS * 2):
        sock.emit('login', {'username': 'gone', 'password': 'the-real-password-1'})

    assert errors(sock)[-1] == store.ERROR_DISABLED
    sock.disconnect()


def test_a_wrong_password_still_counts(store):
    """The fix must not have disabled the throttle altogether."""
    store.create_user('victim', 'the-real-password-1')
    sock = socketio.test_client(flask_app)

    hammer(sock, 'victim', routes.LOGIN_MAX_ATTEMPTS * 2)

    assert any('Too many failed attempts' in e for e in errors(sock))
    sock.disconnect()


def test_the_address_bucket_is_looser_than_the_account_one(store):
    """Everyone behind one router shares an address. If the shared bucket were
    as tight as the per-account one, a single person's typos would lock out
    the room."""
    assert routes.LOGIN_IP_MAX_ATTEMPTS > routes.LOGIN_MAX_ATTEMPTS * 4


def test_one_persons_failures_do_not_immediately_lock_the_address(store):
    """Exhausting one account's budget must leave room for other people."""
    store.create_user('clumsy', 'the-real-password-1')
    store.create_user('colleague', 'another-password-2')

    sock = socketio.test_client(flask_app)
    hammer(sock, 'clumsy', routes.LOGIN_MAX_ATTEMPTS + 3)
    sock.disconnect()

    other = socketio.test_client(flask_app)
    other.emit('login', {'username': 'colleague', 'password': 'another-password-2'})
    assert events(other, 'auth_success')
    other.disconnect()


# ----------------------------------------------------------------------
# The laboratory endpoints
#
# Open on purpose, so the demonstration runs without an account. That makes
# a ceiling on how much CPU one address can take the only thing standing
# between the demo and a free denial of service.
# ----------------------------------------------------------------------

def _tiny_png_data_url():
    import base64
    import cv2
    import numpy as np
    image = np.full((32, 32), 200, np.uint8)
    ok, buf = cv2.imencode('.png', image)
    assert ok
    return 'data:image/png;base64,' + base64.b64encode(buf.tobytes()).decode()


@pytest.fixture(autouse=True)
def _clear_lab_buckets():
    from app.routes import LAB_RATE
    LAB_RATE.clear()
    yield
    LAB_RATE.clear()


@pytest.mark.parametrize('path', ['/api/lab/features', '/api/lab/process'])
def test_lab_endpoints_serve_normal_use(client, path):
    from app.routes import LAB_MAX_REQUESTS
    body = {'image': _tiny_png_data_url()}
    for _ in range(LAB_MAX_REQUESTS):
        assert client.post(path, json=body).status_code == 200


@pytest.mark.parametrize('path', ['/api/lab/features', '/api/lab/process'])
def test_lab_endpoints_refuse_a_flood(client, path):
    from app.routes import LAB_MAX_REQUESTS
    body = {'image': _tiny_png_data_url()}
    for _ in range(LAB_MAX_REQUESTS):
        client.post(path, json=body)

    response = client.post(path, json=body)
    assert response.status_code == 429
    assert response.get_json()['ok'] is False
    assert 'Try again' in response.get_json()['error']


def test_the_two_lab_endpoints_share_one_budget(client):
    """Splitting the flood across both endpoints must not double the budget:
    they cost the same CPU and the limit is on the address, not the route."""
    from app.routes import LAB_MAX_REQUESTS
    body = {'image': _tiny_png_data_url()}
    for i in range(LAB_MAX_REQUESTS):
        path = '/api/lab/features' if i % 2 == 0 else '/api/lab/process'
        assert client.post(path, json=body).status_code == 200

    assert client.post('/api/lab/process', json=body).status_code == 429
    assert client.post('/api/lab/features', json=body).status_code == 429


def test_the_window_expires(client, monkeypatch):
    from app import routes
    body = {'image': _tiny_png_data_url()}
    for _ in range(routes.LAB_MAX_REQUESTS):
        client.post('/api/lab/process', json=body)
    assert client.post('/api/lab/process', json=body).status_code == 429

    real_time = routes.time.time
    monkeypatch.setattr(routes.time, 'time',
                        lambda: real_time() + routes.LAB_WINDOW_SECONDS + 1)
    assert client.post('/api/lab/process', json=body).status_code == 200


def test_the_limit_is_per_address(client):
    """One noisy address must not lock everyone else out of the demo."""
    from app.routes import LAB_MAX_REQUESTS
    body = {'image': _tiny_png_data_url()}
    for _ in range(LAB_MAX_REQUESTS + 1):
        client.post('/api/lab/process', json=body,
                    environ_overrides={'REMOTE_ADDR': '10.0.0.1'})

    other = client.post('/api/lab/process', json=body,
                        environ_overrides={'REMOTE_ADDR': '10.0.0.2'})
    assert other.status_code == 200
