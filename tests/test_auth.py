"""Firebase sign-in and TOTP second factor."""

from unittest.mock import patch

import pyotp
import pytest

from app import app as flask_app, socketio
from app.auth import totp as totp_module
from app.auth.firebase_auth import FirebaseAuthError, claims_to_identity
from app.auth.totp import (
    generate_totp_secret,
    totp_provisioning_uri,
    totp_qr_data_url,
    verify_totp_code,
)

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def code_for(secret):
    return pyotp.TOTP(secret).now()


@pytest.fixture(autouse=True)
def _clean_totp_state():
    totp_module.reset_replay_cache()
    _received.clear()
    yield
    totp_module.reset_replay_cache()


@pytest.fixture
def client_sock(store):
    sock = socketio.test_client(flask_app)
    yield sock
    sock.disconnect()


def signed_in(store, sock, username='alice', password='correct-horse-1'):
    """Create an approved account and sign in with it.

    Self-service registration only files a request now, so it cannot be used
    to reach an authenticated socket.
    """
    store.create_user(username, password)
    sock.emit('login', {'username': username, 'password': password})
    return events(sock, 'auth_success')[0]['args'][0]['user']


# ----------------------------------------------------------------------
# TOTP primitives
# ----------------------------------------------------------------------

def test_secret_and_uri_are_well_formed():
    secret = generate_totp_secret()
    assert len(secret) >= 16 and secret.isalnum()

    uri = totp_provisioning_uri(secret, 'alice@example.com')
    assert uri.startswith('otpauth://totp/')
    assert 'issuer=Reveal-X' in uri
    assert secret in uri


def test_qr_is_a_self_contained_png():
    """Rendered server-side, so the secret never leaves this origin."""
    data_url = totp_qr_data_url(generate_totp_secret(), 'alice')
    assert data_url.startswith('data:image/png;base64,')
    assert len(data_url) > 500


def test_correct_code_verifies_once_and_then_is_refused():
    """Replay protection: an observed code must not work twice."""
    secret = generate_totp_secret()
    code = code_for(secret)

    assert verify_totp_code(secret, code, 'user-1') is True
    assert verify_totp_code(secret, code, 'user-1') is False


def test_replay_cache_is_scoped_per_user():
    secret_a, secret_b = generate_totp_secret(), generate_totp_secret()
    code_a = code_for(secret_a)
    assert verify_totp_code(secret_a, code_a, 'user-1') is True
    # A different account using its own secret is unaffected.
    assert verify_totp_code(secret_b, code_for(secret_b), 'user-2') is True


@pytest.mark.parametrize('bad', ['', '12345', '1234567', 'abcdef', '12 34 56', None])
def test_malformed_codes_are_refused(bad):
    assert verify_totp_code(generate_totp_secret(), bad, 'user-1') is False


def test_a_code_from_another_secret_is_refused():
    assert verify_totp_code(generate_totp_secret(), code_for(generate_totp_secret()), 'u') is False


def test_no_secret_means_no_verification():
    assert verify_totp_code('', '123456', 'u') is False


# ----------------------------------------------------------------------
# Enrolment
# ----------------------------------------------------------------------

def test_enrolment_requires_a_working_code_before_it_takes_effect(store, client_sock):
    """A half-finished setup must not be able to lock anyone out."""
    user = signed_in(store, client_sock)

    client_sock.emit('totp_begin_enrol', {})
    enrolment = events(client_sock, 'totp_enrolment')[0]['args'][0]
    assert enrolment['qr_data_url'].startswith('data:image/png;base64,')

    # Nothing is stored until the code is proven.
    assert store.get_totp_secret(user['id']) == ''

    client_sock.emit('totp_confirm_enrol', {'code': '000000'})
    assert events(client_sock, 'settings_error')
    assert store.get_totp_secret(user['id']) == ''

    client_sock.emit('totp_confirm_enrol', {'code': code_for(enrolment['secret'])})
    assert events(client_sock, 'totp_state')[-1]['args'][0]['enabled'] is True
    assert store.get_totp_secret(user['id']) == enrolment['secret']


def test_enrolment_is_refused_when_already_enabled(store, client_sock):
    user = signed_in(store, client_sock)
    store.set_totp_secret(user['id'], generate_totp_secret())

    client_sock.emit('totp_begin_enrol', {})

    assert events(client_sock, 'totp_enrolment') == []
    assert events(client_sock, 'settings_error')


def test_disabling_needs_a_current_code(store, client_sock):
    user = signed_in(store, client_sock)
    secret = generate_totp_secret()
    store.set_totp_secret(user['id'], secret)

    client_sock.emit('totp_disable', {'code': '000000'})
    assert events(client_sock, 'settings_error')
    assert store.get_totp_secret(user['id']) == secret

    client_sock.emit('totp_disable', {'code': code_for(secret)})
    assert events(client_sock, 'totp_state')[-1]['args'][0]['enabled'] is False
    assert store.get_totp_secret(user['id']) == ''


def test_enrolment_requires_a_session(store, client_sock):
    """An unauthenticated socket cannot start enrolment."""
    client_sock.emit('totp_begin_enrol', {})
    assert events(client_sock, 'totp_enrolment') == []


# ----------------------------------------------------------------------
# TOTP gating a sign-in
# ----------------------------------------------------------------------

def test_password_login_with_totp_yields_no_session_until_verified(store):
    """The whole point: the first factor alone must not grant access."""
    setup = socketio.test_client(flask_app)
    user = signed_in(store, setup)
    secret = generate_totp_secret()
    store.set_totp_secret(user['id'], secret)
    setup.disconnect()

    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})

    assert events(sock, 'totp_required')
    assert events(sock, 'auth_success') == []
    # No session exists, so privileged events do nothing.
    assert chat_room_sessions_for(sock) == 0

    sock.emit('verify_totp', {'code': code_for(secret)})
    assert events(sock, 'auth_success')[0]['args'][0]['user']['id'] == user['id']
    sock.disconnect()


def chat_room_sessions_for(sock):
    from app.models import chat_room
    return len(chat_room.sessions)


def test_wrong_codes_are_counted_and_eventually_abort_the_sign_in(store):
    setup = socketio.test_client(flask_app)
    user = signed_in(store, setup)
    store.set_totp_secret(user['id'], generate_totp_secret())
    setup.disconnect()

    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    assert events(sock, 'totp_required')

    for _ in range(5):
        sock.emit('verify_totp', {'code': '000000'})
    assert events(sock, 'totp_error')

    sock.emit('verify_totp', {'code': '000000'})
    assert events(sock, 'auth_error')
    assert events(sock, 'auth_success') == []
    sock.disconnect()


def test_verify_without_a_pending_sign_in_is_refused(store, client_sock):
    client_sock.emit('verify_totp', {'code': '123456'})
    assert events(client_sock, 'auth_error')
    assert events(client_sock, 'auth_success') == []


def test_accounts_without_totp_sign_in_directly(store, client_sock):
    store.create_user('alice', 'correct-horse-1')
    client_sock.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    assert events(client_sock, 'auth_success')
    assert events(client_sock, 'totp_required') == []


# ----------------------------------------------------------------------
# Firebase
# ----------------------------------------------------------------------

FAKE_CLAIMS = {
    'uid': 'firebase-uid-123',
    'email': 'Alice@Example.com',
    'email_verified': True,
    'name': 'Alice Example',
    'firebase': {'sign_in_provider': 'google.com'},
}


def approve_all(store):
    """Approve every pending account, as an administrator would.

    A first Firebase sign-in only *requests* an account, so tests that care
    about what happens afterwards have to approve it first.
    """
    for account in store.list_accounts():
        if account['account_state'] == 'pending':
            store.set_account_state(account['id'], 'active')


def test_claims_are_normalised():
    identity = claims_to_identity(FAKE_CLAIMS)
    assert identity['firebase_uid'] == 'firebase-uid-123'
    assert identity['email'] == 'alice@example.com'
    assert identity['provider'] == 'google.com'


def test_a_first_google_sign_in_only_requests_an_account(store):
    """Google sign-in must not be a way around the approval policy.

    Without this the whole gate is decorative: anyone with a Google account
    could sign in while password sign-ups sat waiting for an admin.
    """
    with patch('app.routes.verify_id_token', return_value=FAKE_CLAIMS):
        sock = socketio.test_client(flask_app)
        sock.emit('firebase_login', {'id_token': 'pretend-token'})

        assert events(sock, 'auth_success') == []
        assert 'approval' in events(sock, 'auth_error')[0]['args'][0]['error']
        sock.disconnect()

    accounts = store.list_accounts()
    assert [a['account_state'] for a in accounts] == ['pending']


def test_firebase_login_creates_and_then_reuses_one_account(store):
    with patch('app.routes.verify_id_token', return_value=FAKE_CLAIMS):
        first = socketio.test_client(flask_app)
        first.emit('firebase_login', {'id_token': 'pretend-token'})   # request
        first.disconnect()
        approve_all(store)

        _received.clear()
        second = socketio.test_client(flask_app)
        second.emit('firebase_login', {'id_token': 'pretend-token'})
        user_a = events(second, 'auth_success')[0]['args'][0]['user']
        second.disconnect()

        _received.clear()
        third = socketio.test_client(flask_app)
        third.emit('firebase_login', {'id_token': 'pretend-token'})
        user_b = events(third, 'auth_success')[0]['args'][0]['user']
        third.disconnect()

    assert user_a['id'] == user_b['id']
    assert user_a['auth_provider'] == 'google.com'
    assert user_a['email'] == 'alice@example.com'


def test_an_unverifiable_token_grants_nothing(store):
    """Anything the Admin SDK rejects must not reach the account layer."""
    with patch('app.routes.verify_id_token', side_effect=FirebaseAuthError('Sign-in token was rejected')):
        sock = socketio.test_client(flask_app)
        sock.emit('firebase_login', {'id_token': 'forged'})

        assert events(sock, 'auth_error')[0]['args'][0]['error'] == 'Sign-in token was rejected'
        assert events(sock, 'auth_success') == []
        sock.disconnect()


def test_firebase_accounts_do_not_collide_on_username(store):
    """Two Google accounts with the same email local-part still get one row each."""
    other = dict(FAKE_CLAIMS, uid='firebase-uid-456', email='alice@other.example', name='Alice Example')

    with patch('app.routes.verify_id_token', return_value=FAKE_CLAIMS):
        a = socketio.test_client(flask_app)
        a.emit('firebase_login', {'id_token': 't1'})     # request
        a.disconnect()
    with patch('app.routes.verify_id_token', return_value=other):
        b = socketio.test_client(flask_app)
        b.emit('firebase_login', {'id_token': 't2'})     # request
        b.disconnect()
    approve_all(store)

    _received.clear()
    with patch('app.routes.verify_id_token', return_value=FAKE_CLAIMS):
        a = socketio.test_client(flask_app)
        a.emit('firebase_login', {'id_token': 't1'})
        user_a = events(a, 'auth_success')[0]['args'][0]['user']
        a.disconnect()

    _received.clear()
    with patch('app.routes.verify_id_token', return_value=other):
        b = socketio.test_client(flask_app)
        b.emit('firebase_login', {'id_token': 't2'})
        user_b = events(b, 'auth_success')[0]['args'][0]['user']
        b.disconnect()

    assert user_a['id'] != user_b['id']
    assert user_a['username'] != user_b['username']


def test_firebase_sign_in_also_passes_through_totp(store):
    """Google sign-in is a first factor like any other."""
    with patch('app.routes.verify_id_token', return_value=FAKE_CLAIMS):
        setup = socketio.test_client(flask_app)
        setup.emit('firebase_login', {'id_token': 't'})   # request
        setup.disconnect()
        approve_all(store)

        _received.clear()
        setup = socketio.test_client(flask_app)
        setup.emit('firebase_login', {'id_token': 't'})
        user = events(setup, 'auth_success')[0]['args'][0]['user']
        setup.disconnect()

        secret = generate_totp_secret()
        store.set_totp_secret(user['id'], secret)

        _received.clear()
        sock = socketio.test_client(flask_app)
        sock.emit('firebase_login', {'id_token': 't'})

        assert events(sock, 'totp_required')
        assert events(sock, 'auth_success') == []

        sock.emit('verify_totp', {'code': code_for(secret)})
        assert events(sock, 'auth_success')
        sock.disconnect()


def test_firebase_account_never_matched_on_email_alone(store):
    """A local account must not be claimable by signing up with its email."""
    local, error = store.create_user('victim', 'correct-horse-9')
    assert error is None
    store.update_bio(local['id'], 'original owner')

    claims = dict(FAKE_CLAIMS, uid='attacker-uid', email='victim@example.com', name='victim')
    with patch('app.routes.verify_id_token', return_value=claims):
        sock = socketio.test_client(flask_app)
        sock.emit('firebase_login', {'id_token': 'x'})
        sock.disconnect()

    # The attacker's sign-in created its own separate (pending) row and left
    # the existing account untouched -- it did not attach to it.
    created = [a for a in store.list_accounts() if a['id'] != local['id']]
    assert len(created) == 1
    assert created[0]['id'] != local['id']
    assert store.get_user(local['id'])['bio'] == 'original owner'


# ----------------------------------------------------------------------
# Configuration surface
# ----------------------------------------------------------------------

def test_auth_config_reports_no_firebase_when_unconfigured(client):
    payload = client.get('/api/auth/config').get_json()
    assert payload['firebase'] is None
    assert payload['password_login'] is True
    assert payload['totp'] is True


def test_auth_config_never_leaks_the_service_account(client, monkeypatch):
    monkeypatch.setenv('FIREBASE_API_KEY', 'public-api-key')
    monkeypatch.setenv('FIREBASE_AUTH_DOMAIN', 'demo.firebaseapp.com')
    monkeypatch.setenv('FIREBASE_PROJECT_ID', 'demo')
    monkeypatch.setenv('FIREBASE_APP_ID', '1:2:web:3')
    monkeypatch.setenv('FIREBASE_CREDENTIALS_JSON', '{"private_key": "SUPER-SECRET"}')

    body = client.get('/api/auth/config').get_data(as_text=True)
    assert 'SUPER-SECRET' not in body
    assert 'private_key' not in body
