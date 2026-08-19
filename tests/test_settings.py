"""Account settings: rename, bio, availability, and profile photo."""

import base64

import pytest

from app import app as flask_app, socketio


# get_received() empties the queue, so drained events are cached per client and
# accumulated. Tests can then call events() as often as they like.
_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


@pytest.fixture
def pair(store):
    """Two logged-in socket clients: (alice_client, bob_client, alice, bob)."""
    _received.clear()

    # Registration is request-and-approve now, so accounts are created
    # directly (create_user defaults to 'active') and then signed in.
    store.create_user('alice', 'correct-horse-1')
    store.create_user('bob', 'correct-horse-2')

    alice_client = socketio.test_client(flask_app)
    alice_client.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    alice = events(alice_client, 'auth_success')[0]['args'][0]['user']

    bob_client = socketio.test_client(flask_app)
    bob_client.emit('login', {'username': 'bob', 'password': 'correct-horse-2'})
    bob = events(bob_client, 'auth_success')[0]['args'][0]['user']

    _received.clear()
    alice_client.get_received()
    bob_client.get_received()

    yield alice_client, bob_client, alice, bob

    alice_client.disconnect()
    bob_client.disconnect()


# ----------------------------------------------------------------------
# Username
# ----------------------------------------------------------------------

def test_rename_persists_and_is_confirmed(pair, store):
    alice_client, _, alice, _ = pair

    alice_client.emit('update_username', {'new_username': 'alice.new'})

    assert events(alice_client, 'settings_saved')[0]['args'][0]['username'] == 'alice.new'
    assert store.get_user(alice['id'])['username'] == 'alice.new'


def test_rename_updates_the_live_session(pair, store):
    """Messages sent after a rename must carry the new name."""
    alice_client, bob_client, alice, bob = pair

    alice_client.emit('update_username', {'new_username': 'alice.new'})
    alice_client.emit('send_message', {'recipient_id': bob['id'], 'message': 'hello'})

    assert events(bob_client, 'new_message')[0]['args'][0]['username'] == 'alice.new'


def test_rename_is_visible_to_the_other_account(pair):
    alice_client, bob_client, _, _ = pair

    alice_client.emit('update_username', {'new_username': 'alice.new'})

    peers = events(bob_client, 'users_updated')[-1]['args'][0]['users']
    assert [u['username'] for u in peers] == ['alice.new']


def test_rename_to_a_taken_name_is_refused(pair, store):
    alice_client, _, alice, _ = pair

    alice_client.emit('update_username', {'new_username': 'bob'})

    assert events(alice_client, 'settings_error')[0]['args'][0]['error'] == 'Username already exists'
    assert store.get_user(alice['id'])['username'] == 'alice'


def test_rename_is_case_insensitive_about_clashes(pair, store):
    """'Bob' must not be allowed to shadow 'bob'."""
    alice_client, _, alice, _ = pair

    alice_client.emit('update_username', {'new_username': 'BOB'})

    assert events(alice_client, 'settings_error')
    assert store.get_user(alice['id'])['username'] == 'alice'


@pytest.mark.parametrize('name', ['ab', 'has space', 'no!', 'x' * 31, ''])
def test_invalid_usernames_are_refused(pair, store, name):
    alice_client, _, alice, _ = pair

    alice_client.emit('update_username', {'new_username': name})

    assert events(alice_client, 'settings_error')
    assert store.get_user(alice['id'])['username'] == 'alice'


def test_renaming_to_the_same_name_is_a_no_op(pair, store):
    alice_client, _, alice, _ = pair

    alice_client.emit('update_username', {'new_username': 'alice'})

    assert events(alice_client, 'settings_error') == []
    assert store.get_user(alice['id'])['username'] == 'alice'


def test_the_old_name_becomes_available(pair, store):
    alice_client, _, _, _ = pair

    alice_client.emit('update_username', {'new_username': 'alice.new'})
    carol, error = store.create_user('alice', 'correct-horse-3')

    assert error is None and carol['username'] == 'alice'


# ----------------------------------------------------------------------
# Bio and availability
# ----------------------------------------------------------------------

def test_bio_persists_and_reaches_the_peer(pair, store):
    alice_client, bob_client, alice, _ = pair

    alice_client.emit('update_bio', {'bio': 'Security researcher'})

    assert events(alice_client, 'settings_saved')[0]['args'][0]['bio'] == 'Security researcher'
    assert store.get_user(alice['id'])['bio'] == 'Security researcher'

    peers = events(bob_client, 'users_updated')[-1]['args'][0]['users']
    assert peers[0]['bio'] == 'Security researcher'


def test_bio_is_truncated_to_the_documented_limit(pair, store):
    alice_client, _, alice, _ = pair

    alice_client.emit('update_bio', {'bio': 'x' * 400})

    assert len(store.get_user(alice['id'])['bio']) == 150


@pytest.mark.parametrize('status', ['away', 'dnd', 'online'])
def test_status_persists_and_reaches_the_peer(pair, store, status):
    alice_client, bob_client, alice, _ = pair

    alice_client.emit('update_status', {'status': status})

    assert store.get_user(alice['id'])['status'] == status
    peers = events(bob_client, 'users_updated')[-1]['args'][0]['users']
    assert peers[0]['status'] == status


def test_unknown_status_falls_back_to_online(pair, store):
    alice_client, _, alice, _ = pair

    alice_client.emit('update_status', {'status': 'invisible-superuser'})

    assert store.get_user(alice['id'])['status'] == 'online'


def test_offline_users_report_offline_not_their_chosen_status(store, users):
    """A chosen status only means something while connected."""
    alice, _ = users
    store.update_status(alice['id'], 'dnd')

    # Nobody is connected in this fixture, so alice reads as offline.
    listed = store.get_users_list(current_user_id='someone-else')
    assert [u['status'] for u in listed if u['id'] == alice['id']] == ['offline']


# ----------------------------------------------------------------------
# Read receipts
# ----------------------------------------------------------------------

def test_opening_a_chat_marks_it_read_by_default(pair, store):
    alice_client, bob_client, alice, bob = pair

    bob_client.emit('send_message', {'recipient_id': alice['id'], 'message': 'hi'})
    alice_client.emit('select_chat', {'recipient_id': bob['id']})

    assert events(bob_client, 'messages_read')


def test_read_receipts_off_suppresses_the_receipt_on_open(pair, store):
    """Turning receipts off must not leak "Read" just by opening the chat."""
    alice_client, bob_client, alice, bob = pair

    bob_client.emit('send_message', {'recipient_id': alice['id'], 'message': 'hi'})
    alice_client.emit('select_chat', {'recipient_id': bob['id'], 'send_read_receipts': False})

    assert events(bob_client, 'messages_read') == []

    # And the message really is still unread on the server.
    conversation = store.get_conversation(alice['id'], bob['id'])
    assert [m['read_at'] for m in conversation] == [None]


# ----------------------------------------------------------------------
# Profile photo
# ----------------------------------------------------------------------

def test_profile_photo_updates_and_broadcasts(pair, store):
    alice_client, bob_client, alice, _ = pair
    png = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG' + b'\x00' * 64).decode()

    alice_client.emit('update_profile', {'profile_image': png})

    assert events(alice_client, 'profile_updated')[0]['args'][0]['profile_image'] == png
    assert store.get_user(alice['id'])['profile_image'] == png
    peers = events(bob_client, 'users_updated')[-1]['args'][0]['users']
    assert peers[0]['profile_image'] == png


def test_oversized_profile_photo_is_refused(pair, store):
    alice_client, _, alice, _ = pair
    big = 'data:image/png;base64,' + base64.b64encode(b'\x00' * (600 * 1024)).decode()

    alice_client.emit('update_profile', {'profile_image': big})

    assert events(alice_client, 'auth_error')[0]['args'][0]['error'] == 'Profile image is too large'
    assert store.get_user(alice['id'])['profile_image'] == ''


def test_non_image_profile_payload_is_refused(pair, store):
    alice_client, _, alice, _ = pair

    alice_client.emit('update_profile', {'profile_image': 'data:text/html;base64,PHNjcmlwdD4='})

    assert events(alice_client, 'auth_error')
    assert store.get_user(alice['id'])['profile_image'] == ''


# ----------------------------------------------------------------------
# The attack simulator must not exist on the production surface
# ----------------------------------------------------------------------

def test_attack_simulation_handler_is_gone(pair, store):
    """Emitting the removed event must do nothing at all."""
    import os

    import cv2
    import numpy as np

    from app.ai_security import hmac_image
    from app.vc_core import VisualCryptography

    alice_client, bob_client, alice, bob = pair

    share1, _, _ = VisualCryptography().generate_shares_from_image(
        np.tile(np.arange(96, dtype=np.uint8), (96, 1))
    )
    filename = 'share1_sim_test.png'
    path = os.path.join(store.shares_folder, filename)
    cv2.imwrite(path, share1)
    stored = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    digest_before = stored.tobytes()

    store.add_message(
        alice['id'], bob['id'], 'share', 'Shared an encrypted image',
        {'share1_filename': filename, 'share1_token': 'tok',
         'share1_hmac': hmac_image(stored, flask_app.config['SECRET_KEY'])},
    )

    bob_client.emit('simulate_share_attack', {'message_id': 'anything', 'attack': 'block'})

    # The stored share is untouched: no handler exists to modify it.
    assert cv2.imread(path, cv2.IMREAD_GRAYSCALE).tobytes() == digest_before
    assert events(bob_client, 'share_attack_simulated') == []


def test_auth_no_longer_advertises_demo_attacks(pair, store):
    """The client must not be told a simulation feature exists."""
    _, _, _, _ = pair
    store.create_user('carol', 'correct-horse-3')
    client = socketio.test_client(flask_app)
    client.emit('login', {'username': 'carol', 'password': 'correct-horse-3'})

    payload = [e for e in client.get_received() if e['name'] == 'auth_success'][0]['args'][0]
    assert 'demo_attacks' not in payload

    client.disconnect()
