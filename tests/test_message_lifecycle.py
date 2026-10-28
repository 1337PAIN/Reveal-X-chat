"""Revoking, deleting and reading messages.

These handlers decide who can destroy or burn someone else's data, and they were
the least covered code in routes.py. A missing authorisation check here does not
crash or look wrong -- it silently lets the wrong person delete things, which is
the failure mode least likely to be noticed by hand.

Every case therefore drives a real socket and checks both halves: that the
person entitled to act can, and that a third party holding the same message id
cannot.
"""

import base64
import os

import cv2
import numpy as np
import pytest

from app import app as flask_app, socketio

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def last_message(client):
    got = events(client, 'new_message')
    return got[-1]['args'][0] if got else None


@pytest.fixture(autouse=True)
def _clean():
    _received.clear()
    yield
    _received.clear()


def sign_in(store, username, password):
    store.create_user(username, password)
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': username, 'password': password})
    user = events(sock, 'auth_success')[0]['args'][0]['user']
    return sock, user


@pytest.fixture
def trio(store):
    """Alice and Bob talking, plus Mallory -- a legitimate but uninvolved user."""
    a, alice = sign_in(store, 'alice', 'correct-horse-1')
    b, bob = sign_in(store, 'bob', 'correct-horse-2')
    m, mallory = sign_in(store, 'mallory', 'correct-horse-3')
    _received.clear()
    for sock in (a, b, m):
        sock.get_received()
    yield (a, alice), (b, bob), (m, mallory)
    for sock in (a, b, m):
        try:
            sock.disconnect()
        except Exception:
            pass


def png_data_url(image):
    ok, buf = cv2.imencode('.png', image)
    assert ok
    return 'data:image/png;base64,' + base64.b64encode(buf.tobytes()).decode()


def send_text(sender, recipient_id, text='hello'):
    sender.emit('send_message', {'recipient_id': recipient_id, 'message': text})
    return last_message(sender)


def send_share(sender, recipient_id):
    rng = np.random.default_rng(11)
    image = rng.integers(0, 256, (48, 48), dtype=np.uint8)
    sender.emit('send_image', {'recipient_id': recipient_id, 'image': png_data_url(image)})
    return last_message(sender)


# ----------------------------------------------------------------------
# Revoking a share
# ----------------------------------------------------------------------

def test_sender_can_revoke_a_share(trio, store):
    (a, alice), (b, bob), _ = trio
    message = send_share(a, bob['id'])

    a.emit('revoke_share', {'message_id': message['id']})

    assert events(a, 'share_revoked'), 'the sender was told'
    assert events(b, 'share_revoked'), 'the recipient was told'
    stored = store.get_message_for_user(message['id'], alice['id'])
    assert stored['share1_accessed'] is True, 'remaining access was burned'


def test_recipient_can_revoke_a_share(trio, store):
    """Either participant can burn it; the recipient may be the one who notices."""
    (a, alice), (b, bob), _ = trio
    message = send_share(a, bob['id'])

    b.emit('revoke_share', {'message_id': message['id']})

    assert events(b, 'share_revoked')
    assert store.get_message_for_user(message['id'], alice['id'])['share1_accessed'] is True


def test_an_uninvolved_user_cannot_revoke_a_share(trio, store):
    """Knowing the message id must not be enough."""
    (a, alice), (b, bob), (m, _mallory) = trio
    message = send_share(a, bob['id'])

    m.emit('revoke_share', {'message_id': message['id']})

    assert not events(m, 'share_revoked')
    assert not events(a, 'share_revoked'), 'nobody was notified'
    assert store.get_message_for_user(message['id'], alice['id'])['share1_accessed'] is False


def test_revoking_a_text_message_does_nothing(trio, store):
    """The handler is share-specific; a text id must not fall through it."""
    (a, _alice), (b, bob), _ = trio
    message = send_text(a, bob['id'])

    a.emit('revoke_share', {'message_id': message['id']})

    assert not events(a, 'share_revoked')


def test_revoking_an_unknown_id_is_ignored(trio):
    (a, _alice), _, _ = trio
    a.emit('revoke_share', {'message_id': 'no-such-message'})
    a.emit('revoke_share', {})
    assert not events(a, 'share_revoked')


# ----------------------------------------------------------------------
# Deleting
# ----------------------------------------------------------------------

def test_sender_deleting_removes_it_for_both(trio, store):
    (a, alice), (b, bob), _ = trio
    message = send_text(a, bob['id'])

    a.emit('delete_message', {'message_id': message['id']})

    assert events(a, 'message_deleted')[0]['args'][0]['scope'] == 'everyone'
    assert events(b, 'message_deleted'), 'the recipient was told too'
    assert store.get_message_for_user(message['id'], alice['id']) is None
    assert store.get_message_for_user(message['id'], bob['id']) is None


def test_recipient_deleting_leaves_the_sender_copy(trio, store):
    (a, alice), (b, bob), _ = trio
    message = send_text(a, bob['id'])

    b.emit('delete_message', {'message_id': message['id']})

    assert events(b, 'message_deleted')[0]['args'][0]['scope'] == 'self'
    assert store.get_message_for_user(message['id'], bob['id']) is None
    assert store.get_message_for_user(message['id'], alice['id']) is not None, \
        "the sender's own copy must survive"


def test_the_sender_is_not_told_when_the_recipient_hides_a_message(trio):
    """A 'self' delete is private. Telling the sender leaks that it was read."""
    (a, _alice), (b, bob), _ = trio
    message = send_text(a, bob['id'])
    a.get_received()

    b.emit('delete_message', {'message_id': message['id']})

    assert not events(a, 'message_deleted')


def test_an_uninvolved_user_cannot_delete(trio, store):
    (a, alice), (b, bob), (m, _mallory) = trio
    message = send_text(a, bob['id'])

    m.emit('delete_message', {'message_id': message['id']})

    assert not events(m, 'message_deleted')
    assert store.get_message_for_user(message['id'], alice['id']) is not None
    assert store.get_message_for_user(message['id'], bob['id']) is not None


def test_deleting_a_share_removes_share_one_from_disk(trio, store):
    """The row going is not enough -- the image must leave the filesystem."""
    (a, alice), (b, bob), _ = trio
    message = send_share(a, bob['id'])

    stored = store.get_message_for_user(message['id'], alice['id'])
    path = os.path.join(store.shares_folder, stored['extra']['share1_filename'])
    assert os.path.exists(path), 'precondition: the share was written'

    a.emit('delete_message', {'message_id': message['id']})

    assert not os.path.exists(path), 'Share 1 was left behind on disk'


def test_recipient_deleting_a_share_leaves_the_file_for_the_sender(trio, store):
    """A recipient hiding their copy must not destroy the sender's."""
    (a, alice), (b, bob), _ = trio
    message = send_share(a, bob['id'])
    stored = store.get_message_for_user(message['id'], alice['id'])
    path = os.path.join(store.shares_folder, stored['extra']['share1_filename'])

    b.emit('delete_message', {'message_id': message['id']})

    assert os.path.exists(path)


def test_deleting_twice_is_harmless(trio):
    (a, _alice), (b, bob), _ = trio
    message = send_text(a, bob['id'])

    b.emit('delete_message', {'message_id': message['id']})
    b.emit('delete_message', {'message_id': message['id']})

    assert len(events(b, 'message_deleted')) == 2, 'still acknowledged'


def test_deleting_an_unknown_id_is_ignored(trio):
    (a, _alice), _, _ = trio
    a.emit('delete_message', {'message_id': 'no-such-message'})
    a.emit('delete_message', {})
    assert not events(a, 'message_deleted')


# ----------------------------------------------------------------------
# Deleting an account
# ----------------------------------------------------------------------

def test_deleting_an_account_removes_it_and_its_messages(trio, store):
    (a, alice), (b, bob), _ = trio
    sent = send_text(a, bob['id'], 'from alice')
    received = send_text(b, alice['id'], 'to alice')

    a.emit('delete_account', {'password': 'correct-horse-1'})

    assert events(a, 'logout_success')
    assert store.authenticate('alice', 'correct-horse-1')[0] is None, 'account gone'
    assert store.get_message_for_user(sent['id'], bob['id']) is None
    assert store.get_message_for_user(received['id'], bob['id']) is None


def test_deleting_an_account_leaves_other_conversations_alone(trio, store):
    """Blast radius: one account leaving must not touch anyone else's history."""
    (a, _alice), (b, bob), (m, mallory) = trio
    between_others = send_text(b, mallory['id'], 'unrelated')

    a.emit('delete_account', {'password': 'correct-horse-1'})

    assert events(a, 'logout_success'), 'the deletion actually happened'
    assert store.get_message_for_user(between_others['id'], bob['id']) is not None
    assert store.get_message_for_user(between_others['id'], mallory['id']) is not None
    assert store.authenticate('bob', 'correct-horse-2')[0] is not None


# Deletion is irreversible and used to ask for nothing, so an unlocked tab was
# enough to destroy an account and every message in it. The project already
# held `totp_disable` to this standard -- a current code, so a borrowed session
# cannot quietly remove the second factor -- while deletion, which is strictly
# more destructive, was held to none.

@pytest.mark.parametrize('payload, why', [
    ({}, 'no password at all'),
    ({'password': ''}, 'an empty password'),
    ({'password': 'correct-horse-2'}, "another user's password"),
    ({'password': 'Correct-Horse-1'}, 'the right password in the wrong case'),
])
def test_deleting_an_account_refuses_without_the_password(trio, store, payload, why):
    (a, _alice), (b, bob), _ = trio
    kept = send_text(a, bob['id'], 'still here')

    a.emit('delete_account', payload)

    assert events(a, 'settings_error'), 'refused: ' + why
    assert not events(a, 'logout_success')
    assert store.authenticate('alice', 'correct-horse-1')[0] is not None, (
        'the account survived ' + why
    )
    assert store.get_message_for_user(kept['id'], bob['id']) is not None


def test_a_failed_deletion_does_not_mention_being_an_administrator(store):
    """The refusal has to be about the password. Saying "you are the only
    administrator" to someone who could not produce the password tells an
    attacker holding a borrowed session exactly whose account they have."""
    sock = socketio.test_client(flask_app)
    store.create_user('soleadmin', 'correct-horse-8', role='admin')
    sock.emit('login', {'username': 'soleadmin', 'password': 'correct-horse-8'})
    events(sock, 'auth_success')

    sock.emit('delete_account', {'password': 'not-the-password'})

    error = events(sock, 'settings_error')[-1]['args'][0]['error'].lower()
    assert 'password' in error
    assert 'admin' not in error, 'the refusal leaked why this account matters'
    sock.disconnect()


def test_the_last_administrator_cannot_delete_themselves(store):
    """Nothing stopped this before. The rank rules keep one admin from removing
    another, but the last one could remove themselves and leave nobody able to
    approve a registration or reinstate anyone -- recoverable only by
    restarting the server with REVEALX_ADMIN_USER set."""
    sock = socketio.test_client(flask_app)
    store.create_user('onlyadmin', 'correct-horse-9', role='admin')
    sock.emit('login', {'username': 'onlyadmin', 'password': 'correct-horse-9'})
    events(sock, 'auth_success')

    sock.emit('delete_account', {'password': 'correct-horse-9'})

    assert events(sock, 'settings_error'), 'refused'
    assert not events(sock, 'logout_success')
    assert store.authenticate('onlyadmin', 'correct-horse-9')[0] is not None
    assert store.count_admins() == 1, 'the server still has someone in charge'
    sock.disconnect()


def test_an_administrator_who_is_not_the_last_one_may_leave(store):
    """The guard is about the server keeping an administrator, not about
    administrators being undeletable."""
    sock = socketio.test_client(flask_app)
    store.create_user('leaving', 'correct-horse-10', role='admin')
    store.create_user('staying', 'correct-horse-11', role='admin')
    sock.emit('login', {'username': 'leaving', 'password': 'correct-horse-10'})
    events(sock, 'auth_success')

    sock.emit('delete_account', {'password': 'correct-horse-10'})

    assert events(sock, 'logout_success'), 'allowed'
    assert store.authenticate('leaving', 'correct-horse-10')[0] is None
    assert store.count_admins() == 1
    sock.disconnect()


def test_deleting_an_account_signs_out_its_other_devices(store):
    """The account is about to stop existing. A second tab holding it would
    otherwise keep chatting as a user the database no longer has -- the same
    reason admin deletion kicks every session."""
    first = socketio.test_client(flask_app)
    second = socketio.test_client(flask_app)
    store.create_user('twodevice', 'correct-horse-12')
    for sock in (first, second):
        sock.emit('login', {'username': 'twodevice', 'password': 'correct-horse-12'})
        events(sock, 'auth_success')

    first.emit('delete_account', {'password': 'correct-horse-12'})

    assert events(second, 'force_signed_out'), 'the other device was told'
    for sock in (first, second):
        sock.disconnect()


def test_delete_account_requires_a_session(store):
    """An unauthenticated socket must not be able to delete anything."""
    sock = socketio.test_client(flask_app)
    sock.get_received()

    sock.emit('delete_account', {})

    assert events(sock, 'auth_error'), 'refused'
    assert not events(sock, 'logout_success')
    sock.disconnect()


# ----------------------------------------------------------------------
# Read receipts and typing
# ----------------------------------------------------------------------

def test_typing_reaches_only_the_named_recipient(trio):
    (a, _alice), (b, bob), (m, _mallory) = trio

    a.emit('typing', {'recipient_id': bob['id'], 'is_typing': True})

    assert events(b, 'typing'), 'the recipient saw it'
    assert not events(m, 'typing'), 'nobody else did'


def test_marking_read_tells_the_sender(trio):
    (a, alice), (b, bob), (m, _mallory) = trio
    send_text(a, bob['id'])

    b.emit('mark_read', {'sender_id': alice['id']})

    assert events(a, 'messages_read'), 'the sender was told their message was read'
    assert not events(m, 'messages_read')


def test_typing_without_a_session_is_ignored(store):
    sock = socketio.test_client(flask_app)
    sock.get_received()
    sock.emit('typing', {'recipient_id': 'anyone', 'is_typing': True})
    assert not events(sock, 'typing')
    sock.disconnect()
