"""TOTP secrets are encrypted at rest when REVEALX_DATA_KEY is set.

A TOTP secret is not like a password hash: the server has to be able to read
it back to check a code, so it was stored in the clear. Anyone with a copy of
the database -- a backup, a leaked dump, a hosting snapshot -- could then
generate valid second-factor codes for every account that had one, for ever.

With REVEALX_DATA_KEY set, each secret is AES-GCM encrypted under that key
with the user's id as associated data, existing plaintext rows are migrated
on startup, and the app refuses to start if encrypted rows exist and the key
is missing or wrong, rather than quietly treating everyone's second factor as
switched off.
"""

import base64
import os

import pyotp
import pytest

from app import app as flask_app, socketio
from app.auth.totp import generate_totp_secret

KEY = base64.b64encode(b'k' * 32).decode()
OTHER_KEY = base64.b64encode(b'z' * 32).decode()


def raw_secret(store, user_id):
    with store._connect() as db:
        # By name, so it reads the same on SQLite and PostgreSQL.
        return db.execute("SELECT totp_secret FROM users WHERE id = ?", (user_id,)).fetchone()['totp_secret']


@pytest.fixture
def alice(store):
    user, _ = store.create_user('alice', 'correct-horse-1')
    return user


def test_with_a_key_the_secret_is_stored_encrypted(store, alice, monkeypatch):
    monkeypatch.setenv('REVEALX_DATA_KEY', KEY)
    secret = generate_totp_secret()
    store.set_totp_secret(alice['id'], secret)

    stored = raw_secret(store, alice['id'])
    assert stored.startswith('enc:v1:')
    assert secret not in stored
    assert store.get_totp_secret(alice['id']) == secret


def test_sign_in_with_an_encrypted_secret_still_works(store, alice, monkeypatch):
    monkeypatch.setenv('REVEALX_DATA_KEY', KEY)
    secret = generate_totp_secret()
    store.set_totp_secret(alice['id'], secret)

    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    sock.emit('verify_totp', {'code': pyotp.TOTP(secret).now()})
    assert any(e['name'] == 'auth_success' for e in sock.get_received())
    sock.disconnect()


def test_existing_plaintext_rows_are_migrated_on_startup(store, alice, monkeypatch):
    monkeypatch.delenv('REVEALX_DATA_KEY', raising=False)
    secret = generate_totp_secret()
    store.set_totp_secret(alice['id'], secret)
    assert raw_secret(store, alice['id']) == secret

    monkeypatch.setenv('REVEALX_DATA_KEY', KEY)
    store.init_db()
    assert raw_secret(store, alice['id']).startswith('enc:v1:')
    assert store.get_totp_secret(alice['id']) == secret


def test_encrypted_rows_without_the_key_stop_the_app(store, alice, monkeypatch):
    """Treating an unreadable secret as "no second factor" would switch 2FA
    off for every account the moment the key went missing."""
    monkeypatch.setenv('REVEALX_DATA_KEY', KEY)
    store.set_totp_secret(alice['id'], generate_totp_secret())

    monkeypatch.delenv('REVEALX_DATA_KEY')
    with pytest.raises(RuntimeError, match='REVEALX_DATA_KEY'):
        store.init_db()
    with pytest.raises(RuntimeError):
        store.get_totp_secret(alice['id'])


def test_the_wrong_key_stops_the_app(store, alice, monkeypatch):
    monkeypatch.setenv('REVEALX_DATA_KEY', KEY)
    store.set_totp_secret(alice['id'], generate_totp_secret())
    monkeypatch.setenv('REVEALX_DATA_KEY', OTHER_KEY)
    with pytest.raises(RuntimeError, match='REVEALX_DATA_KEY'):
        store.init_db()


@pytest.mark.parametrize('bad', ['short', base64.b64encode(b'x' * 16).decode(), '!!!not base64!!!'])
def test_a_malformed_key_stops_the_app(store, monkeypatch, bad):
    monkeypatch.setenv('REVEALX_DATA_KEY', bad)
    with pytest.raises(RuntimeError, match='REVEALX_DATA_KEY'):
        store.init_db()


def test_a_ciphertext_moved_to_another_account_does_not_open(store, alice, monkeypatch):
    """The user id is authenticated, so copying one account's encrypted secret
    onto another row (with write access to the database) gains nothing."""
    monkeypatch.setenv('REVEALX_DATA_KEY', KEY)
    bob, _ = store.create_user('bob', 'correct-horse-2')
    store.set_totp_secret(alice['id'], generate_totp_secret())
    with store._connect() as db:
        db.execute("UPDATE users SET totp_secret = ? WHERE id = ?",
                   (raw_secret(store, alice['id']), bob['id']))
    with pytest.raises(RuntimeError):
        store.get_totp_secret(bob['id'])


def test_turning_two_factor_off_stores_nothing(store, alice, monkeypatch):
    monkeypatch.setenv('REVEALX_DATA_KEY', KEY)
    store.set_totp_secret(alice['id'], generate_totp_secret())
    store.set_totp_secret(alice['id'], '')
    assert raw_secret(store, alice['id']) == ''
    assert store.get_totp_secret(alice['id']) == ''


def test_without_a_key_nothing_changes(store, alice, monkeypatch):
    """Development keeps working with no configuration; DEPLOYMENT.md says to
    set the key in production."""
    monkeypatch.delenv('REVEALX_DATA_KEY', raising=False)
    secret = generate_totp_secret()
    store.set_totp_secret(alice['id'], secret)
    assert store.get_totp_secret(alice['id']) == secret
