"""Locking a chat behind a PIN, and blocking someone.

Lock chat: a 4-8 digit chat-lock PIN, set once, stored as an Argon2id hash
(migration 14). A locked chat is listed as locked and its history is not
served until this session has entered the PIN; five wrong PINs in fifteen
minutes and it waits. Changing the PIN needs the current one; a forgotten PIN
can be reset, which unlocks the chats by clearing their messages for you, so
the reset gives away nothing the PIN was guarding.

Block: ends the contact, and the blocked person can neither message you nor
reach you with a contact request -- theirs is kept on their side as
"waiting", so blocking is not announced (migration 15).
"""

import pytest

from app import app as flask_app, socketio
from app.handlers import common

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def last(client, name):
    found = events(client, name)
    return found[-1]['args'][0] if found else None


def roster(client):
    return {u['username']: u for u in last(client, 'users_updated')['users']}


@pytest.fixture
def world(store):
    _received.clear()
    common.CHAT_LOCK_OPEN.clear()
    if hasattr(common.CHAT_LOCK_RATE, 'clear'):
        common.CHAT_LOCK_RATE.clear()
    alice, _ = store.create_user('alice', 'correct-horse-1')
    bob, _ = store.create_user('bob', 'correct-horse-2')
    a = socketio.test_client(flask_app)
    a.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    b = socketio.test_client(flask_app)
    b.emit('login', {'username': 'bob', 'password': 'correct-horse-2'})
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'secret'})
    yield a, b, alice, bob
    a.disconnect()
    b.disconnect()


def lock_bob(a, bob, pin='2468'):
    a.emit('chat_lock_set_pin', {'pin': pin})
    a.emit('chat_lock', {'peer_id': bob['id'], 'value': True})


# --- the PIN ------------------------------------------------------------------

def test_sign_in_says_whether_a_pin_is_set(world):
    a, *_ = world
    assert events(a, 'chat_lock_state')[0]['args'][0] == {'pin_set': False, 'open': False}


@pytest.mark.parametrize('pin', ['123', '123456789', '12a4', '', None, 1234])
def test_a_pin_is_four_to_eight_digits(world, store, pin):
    a, b, alice, bob = world
    a.emit('chat_lock_set_pin', {'pin': pin})
    assert last(a, 'chat_lock_error')
    assert not store.chat_lock_pin_set(alice['id'])


def test_the_pin_is_stored_hashed(world, store):
    a, b, alice, bob = world
    a.emit('chat_lock_set_pin', {'pin': '2468'})
    with store._connect() as db:
        stored = db.execute("SELECT chat_lock_hash FROM users WHERE id = ?", (alice['id'],)).fetchone()[0]
    assert stored and '2468' not in stored and stored.startswith('$argon2')
    assert last(a, 'chat_lock_state') == {'pin_set': True, 'open': True}


def test_changing_the_pin_needs_the_current_one(world, store):
    a, b, alice, bob = world
    a.emit('chat_lock_set_pin', {'pin': '2468'})
    a.emit('chat_lock_set_pin', {'pin': '1357', 'current_pin': '0000'})
    assert store.verify_chat_lock_pin(alice['id'], '2468')
    a.emit('chat_lock_set_pin', {'pin': '1357', 'current_pin': '2468'})
    assert store.verify_chat_lock_pin(alice['id'], '1357')


# --- locking ------------------------------------------------------------------

def test_locking_needs_a_pin_first(world, store):
    a, b, alice, bob = world
    a.emit('chat_lock', {'peer_id': bob['id'], 'value': True})
    assert last(a, 'chat_lock_error')['error'] == common.CHAT_LOCK_NEEDS_PIN
    assert roster(a)['bob']['locked'] is False


def test_a_locked_chat_is_listed_as_locked_for_you_only(world, store):
    a, b, alice, bob = world
    lock_bob(a, bob)
    assert roster(a)['bob']['locked'] is True
    assert store.get_users_list(bob['id'])[0]['locked'] is False


def test_a_locked_chat_is_not_served_until_the_pin_is_entered(world):
    a, b, alice, bob = world
    lock_bob(a, bob)
    a.emit('chat_lock_close', {})
    a.emit('select_chat', {'recipient_id': bob['id']})
    assert last(a, 'chat_history')['messages'] == []
    assert last(a, 'chat_history')['locked'] is True

    a.emit('chat_lock_open', {'pin': '2468'})
    assert last(a, 'chat_lock_state') == {'pin_set': True, 'open': True}
    a.emit('select_chat', {'recipient_id': bob['id']})
    assert [m['content'] for m in last(a, 'chat_history')['messages']] == ['secret']


def test_another_session_needs_the_pin_too(world, store):
    a, b, alice, bob = world
    lock_bob(a, bob)
    a2 = socketio.test_client(flask_app)
    a2.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    a2.emit('select_chat', {'recipient_id': bob['id']})
    assert last(a2, 'chat_history')['messages'] == []
    a2.disconnect()


def test_wrong_pins_are_limited(world):
    a, b, alice, bob = world
    lock_bob(a, bob)
    a.emit('chat_lock_close', {})
    for _ in range(common.CHAT_LOCK_MAX_FAILURES):
        a.emit('chat_lock_open', {'pin': '0000'})
    a.emit('chat_lock_open', {'pin': '2468'})        # right, but too late
    assert 'Too many' in last(a, 'chat_lock_error')['error']
    assert last(a, 'chat_lock_state')['open'] is False


def test_unlocking_a_chat_needs_the_pin_entered(world):
    a, b, alice, bob = world
    lock_bob(a, bob)
    a.emit('chat_lock_close', {})
    a.emit('chat_lock', {'peer_id': bob['id'], 'value': False})
    assert roster(a)['bob']['locked'] is True
    a.emit('chat_lock_open', {'pin': '2468'})
    a.emit('chat_lock', {'peer_id': bob['id'], 'value': False})
    assert roster(a)['bob']['locked'] is False


def test_a_forgotten_pin_resets_by_clearing_the_locked_chats(world, store):
    a, b, alice, bob = world
    lock_bob(a, bob)
    a.emit('chat_lock_close', {})
    a.emit('chat_lock_reset', {})
    assert not store.chat_lock_pin_set(alice['id'])
    assert roster(a)['bob']['locked'] is False
    assert store.get_conversation(alice['id'], bob['id']) == []           # cleared for alice
    assert [m['content'] for m in store.get_conversation(bob['id'], alice['id'])] == ['secret']


# --- blocking -----------------------------------------------------------------

def test_blocking_ends_the_contact_and_stops_messages(world, store):
    a, b, alice, bob = world
    a.emit('block_user', {'user_id': bob['id']})
    assert not store.are_contacts(alice['id'], bob['id'])
    assert [u['username'] for u in last(a, 'blocked_updated')['blocked']] == ['bob']
    b.emit('send_message', {'recipient_id': alice['id'], 'message': 'hello?'})
    assert last(b, 'message_error')['error'] == common.NOT_A_CONTACT


def test_a_blocked_persons_request_never_arrives(world, store):
    a, b, alice, bob = world
    a.emit('block_user', {'user_id': bob['id']})
    b.emit('contact_request', {'username': 'alice'})
    assert last(b, 'contact_result')['status'] == 'requested'      # not told
    assert store.contact_requests(alice['id'])['incoming'] == []
    assert [r['username'] for r in store.contact_requests(bob['id'])['outgoing']] == ['alice']


def test_you_cannot_add_someone_you_blocked(world):
    a, b, alice, bob = world
    a.emit('block_user', {'user_id': bob['id']})
    a.emit('contact_request', {'username': 'bob'})
    assert last(a, 'contact_result')['error'] == common.YOU_BLOCKED_THEM


def test_unblocking_lets_them_ask_again(world, store):
    a, b, alice, bob = world
    a.emit('block_user', {'user_id': bob['id']})
    a.emit('unblock_user', {'user_id': bob['id']})
    assert last(a, 'blocked_updated')['blocked'] == []
    b.emit('contact_request', {'username': 'alice'})
    assert [r['username'] for r in store.contact_requests(alice['id'])['incoming']] == ['bob']


def test_deleting_an_account_removes_its_blocks(world, store):
    a, b, alice, bob = world
    a.emit('block_user', {'user_id': bob['id']})
    store.purge_user(bob['id'])
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM blocks").fetchone()['n'] == 0


def test_the_first_lock_sets_the_pin_and_locks_in_one_step(world):
    """Two events could be handled out of order (they run in parallel and the
    hash is slow); the first lock therefore goes with the PIN."""
    a, b, alice, bob = world
    a.emit('chat_lock_set_pin', {'pin': '2468', 'lock_peer_id': bob['id']})
    assert roster(a)['bob']['locked'] is True
