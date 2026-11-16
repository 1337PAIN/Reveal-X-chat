"""Contacts: you see and message only the people you have added.

Every account used to see every other account and could message any of them.
Now a person is added by exact username; they get a request they can accept
or decline, and until they accept neither side can message the other, see
the other in their list, or see whether they are online. The server enforces
it -- every send path, typing, and opening a chat's history -- not just the
list. Conversations that already existed when this shipped were kept: the
pairs in them became contacts (migration 11).

These tests opt out of conftest's shortcut that makes every test account a
contact of every other (the `real_contacts` marker).
"""

import base64
import uuid

import pytest

from app import app as flask_app, socketio
from app import migrations
from app.handlers import common
from e2ee_payload import epk, now_ms

pytestmark = pytest.mark.real_contacts

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def last(client, name):
    found = events(client, name)
    return found[-1]['args'][0] if found else None


def roster_names(client):
    users = last(client, 'users_updated') or {}
    return sorted(u['username'] for u in users.get('users', []))


def sign_in(username, password):
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': username, 'password': password})
    assert events(sock, 'auth_success'), username
    return sock


@pytest.fixture
def world(store):
    _received.clear()
    alice, _ = store.create_user('alice', 'correct-horse-1')
    bob, _ = store.create_user('bob', 'correct-horse-2')
    carol, _ = store.create_user('carol', 'correct-horse-3')
    a = sign_in('alice', 'correct-horse-1')
    b = sign_in('bob', 'correct-horse-2')
    yield a, b, alice, bob, carol
    a.disconnect()
    b.disconnect()


def befriend(a, b, bob):
    a.emit('contact_request', {'username': 'bob'})
    requests = last(b, 'contacts_updated')
    b.emit('contact_respond', {'user_id': requests['incoming'][0]['id'], 'accept': True})


# --- the roster ---------------------------------------------------------------

def test_a_new_account_sees_nobody(world):
    a, b, *_ = world
    auth = events(a, 'auth_success')[0]['args'][0]
    assert auth['users'] == []


def test_the_roster_lists_accepted_contacts_only(world, store):
    a, b, alice, bob, carol = world
    store.request_contact(alice['id'], 'carol')            # pending, not accepted
    befriend(a, b, bob)
    names = [u['username'] for u in store.get_users_list(alice['id'])]
    assert names == ['bob']


# --- requesting ---------------------------------------------------------------

def test_a_request_by_username_reaches_the_other_person(world):
    a, b, alice, bob, carol = world
    a.emit('contact_request', {'username': '  @BOB '})     # case, spaces and @ forgiven
    result = last(a, 'contact_result')
    assert result['ok'] is True and result['status'] == 'requested'
    incoming = last(b, 'contacts_updated')['incoming']
    assert [r['username'] for r in incoming] == ['alice']
    assert set(incoming[0]) == {'id', 'username', 'profile_image'}
    outgoing = last(a, 'contacts_updated')['outgoing']
    assert [r['username'] for r in outgoing] == ['bob']
    # Asked is not accepted: still not in each other's list.
    assert roster_names(a) == [] and roster_names(b) == []


@pytest.mark.parametrize('name', ['nobody', 'pendingone', 'disabledone', 'root'])
def test_an_account_you_cannot_add_looks_like_no_account(world, store, name):
    """Pending, disabled and admin accounts answer exactly as a name nobody
    has, so the field cannot be used to learn which of them exist."""
    a, *_ = world
    pending, _ = store.create_user('pendingone', 'correct-horse-4')
    store.set_account_state(pending['id'], 'pending')
    disabled, _ = store.create_user('disabledone', 'correct-horse-5')
    store.set_account_state(disabled['id'], 'disabled')
    store.create_user('root', 'correct-horse-6', role='admin')
    a.emit('contact_request', {'username': name})
    result = last(a, 'contact_result')
    assert result == {'ok': False, 'error': common.NO_SUCH_ACCOUNT}


def test_you_cannot_add_yourself(world):
    a, *_ = world
    a.emit('contact_request', {'username': 'alice'})
    assert last(a, 'contact_result')['ok'] is False


def test_asking_twice_is_not_two_requests(world, store):
    a, b, alice, bob, carol = world
    a.emit('contact_request', {'username': 'bob'})
    a.emit('contact_request', {'username': 'bob'})
    assert last(a, 'contact_result')['status'] == 'already_requested'
    assert len(store.contact_requests(bob['id'])['incoming']) == 1


def test_two_people_asking_each_other_are_contacts(world):
    a, b, alice, bob, carol = world
    a.emit('contact_request', {'username': 'bob'})
    b.emit('contact_request', {'username': 'alice'})
    assert last(b, 'contact_result')['status'] == 'accepted'
    assert roster_names(a) == ['bob'] and roster_names(b) == ['alice']


def test_requests_are_rate_limited(world):
    a, *_ = world
    capacity = common.SEND_LIMITS['contact'][0]
    for i in range(capacity + 3):
        a.emit('contact_request', {'username': f'nobody{i}'})
    assert last(a, 'contact_result')['error'] == common.SEND_TOO_FAST


# --- answering ----------------------------------------------------------------

def test_accepting_puts_each_in_the_others_list(world, store):
    a, b, alice, bob, carol = world
    befriend(a, b, bob)
    assert store.are_contacts(alice['id'], bob['id'])
    assert roster_names(a) == ['bob'] and roster_names(b) == ['alice']
    assert last(a, 'contacts_updated')['outgoing'] == []


def test_declining_leaves_no_trace(world, store):
    a, b, alice, bob, carol = world
    a.emit('contact_request', {'username': 'bob'})
    b.emit('contact_respond', {'user_id': alice['id'], 'accept': False})
    assert not store.are_contacts(alice['id'], bob['id'])
    assert store.contact_requests(alice['id']) == {'incoming': [], 'outgoing': []}
    assert store.contact_requests(bob['id']) == {'incoming': [], 'outgoing': []}


def test_only_the_person_asked_can_accept(world, store):
    a, b, alice, bob, carol = world
    a.emit('contact_request', {'username': 'bob'})
    a.emit('contact_respond', {'user_id': bob['id'], 'accept': True})
    assert not store.are_contacts(alice['id'], bob['id'])


def test_cancelling_a_request_withdraws_it(world, store):
    a, b, alice, bob, carol = world
    a.emit('contact_request', {'username': 'bob'})
    a.emit('contact_remove', {'user_id': bob['id']})
    assert store.contact_requests(bob['id'])['incoming'] == []
    assert last(b, 'contacts_updated')['incoming'] == []


def test_removing_a_contact_ends_it_for_both(world, store):
    a, b, alice, bob, carol = world
    befriend(a, b, bob)
    b.emit('contact_remove', {'user_id': alice['id']})
    assert not store.are_contacts(alice['id'], bob['id'])
    assert roster_names(a) == [] and roster_names(b) == []


# --- enforcement --------------------------------------------------------------

def sends(recipient_id):
    return [
        ('send_message', {'recipient_id': recipient_id, 'message': 'hello'}),
        ('send_message', {'recipient_id': recipient_id, 'message': 'Y2lwaGVy', 'iv': 'aXY=',
                          'encrypted': True, 'v': 4, 'sent_at': now_ms(), 'epk': epk(),
                          'msg_uuid': str(uuid.uuid4())}),
        ('send_voice', {'recipient_id': recipient_id, 'audio': base64.b64encode(b'\x01' * 64).decode(),
                        'iv': 'aXY=', 'mime': 'audio/webm', 'encrypted': True, 'v': 4,
                        'sent_at': now_ms(), 'epk': epk(), 'msg_uuid': str(uuid.uuid4())}),
    ]


def test_strangers_cannot_message_each_other(world, store):
    a, b, alice, bob, carol = world
    for event, payload in sends(bob['id']):
        a.emit(event, payload)
    assert store.get_conversation(alice['id'], bob['id']) == []
    errors = [e['args'][0]['error'] for e in events(a, 'message_error')]
    assert errors and all(error == common.NOT_A_CONTACT for error in errors)


def test_a_pending_request_does_not_open_the_conversation(world, store):
    a, b, alice, bob, carol = world
    a.emit('contact_request', {'username': 'bob'})
    for event, payload in sends(bob['id']):
        a.emit(event, payload)
    assert store.get_conversation(alice['id'], bob['id']) == []


def test_contacts_can_message_each_other(world, store):
    a, b, alice, bob, carol = world
    befriend(a, b, bob)
    for event, payload in sends(bob['id']):
        a.emit(event, payload)
    assert len(store.get_conversation(alice['id'], bob['id'])) == 3


def test_an_image_share_to_a_stranger_is_refused_before_anything_else(world):
    a, b, alice, bob, carol = world
    a.emit('send_share', {'recipient_id': bob['id']})
    assert last(a, 'image_error')['error'] == common.NOT_A_CONTACT


def test_a_stranger_does_not_see_you_typing(world):
    a, b, alice, bob, carol = world
    events(b, 'typing')
    a.emit('typing', {'recipient_id': bob['id'], 'is_typing': True})
    assert events(b, 'typing') == []


def test_a_stranger_cannot_forward_to_you(world, store):
    a, b, alice, bob, carol = world
    a.emit('forward_message', {'message_id': 'anything', 'recipient_id': bob['id']})
    assert last(a, 'message_error')['error'] == common.NOT_A_CONTACT


def test_after_removal_the_old_conversation_is_not_served(world, store):
    a, b, alice, bob, carol = world
    befriend(a, b, bob)
    a.emit('send_message', {'recipient_id': bob['id'], 'message': 'before'})
    b.emit('contact_remove', {'user_id': alice['id']})
    a.emit('select_chat', {'recipient_id': bob['id']})
    assert last(a, 'chat_history')['messages'] == []


def test_the_storage_layer_refuses_strangers_too(world, store):
    """Defence in depth: a future send path that forgets the check still
    cannot write a row between people who are not contacts."""
    a, b, alice, bob, carol = world
    assert store.add_message(alice['id'], carol['id'], 'text', 'x') is None


# --- lifecycle ----------------------------------------------------------------

def test_existing_conversations_become_contacts(store):
    """Migration 11 keeps every chat people already had."""
    alice, _ = store.create_user('alice', 'correct-horse-1')
    bob, _ = store.create_user('bob', 'correct-horse-2')
    carol, _ = store.create_user('carol', 'correct-horse-3')
    with store._connect() as db:
        db.execute(
            "INSERT INTO messages (id, sender_id, recipient_id, type, content, extra, created_at) "
            "VALUES ('m1', ?, ?, 'text', 'hi', '{}', '2026-11-17T00:00:00')",
            (bob['id'], alice['id']))
        db.execute("DELETE FROM contacts")
        migrations.m011_contacts(store, db)
    assert store.are_contacts(alice['id'], bob['id'])
    assert not store.are_contacts(alice['id'], carol['id'])


def test_deleting_an_account_removes_its_contacts(world, store):
    a, b, alice, bob, carol = world
    befriend(a, b, bob)
    store.request_contact(carol['id'], 'alice')
    store.purge_user(alice['id'])
    with store._connect() as db:
        left = db.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()['n']
    assert left == 0
