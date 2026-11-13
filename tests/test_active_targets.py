"""Messages and group invites reach active accounts only.

Pending and disabled accounts were filtered out of the roster, but every send
path checked only that the recipient *existed*. Anyone who knew or guessed an
account id could message an unapproved registration, or a disabled account,
and the rows sat in the database for that account to read the moment it was
approved or reinstated. Group creation and invitation took the same
shortcut, so a disabled account could be pulled into a group.

The refusal is the same one an unknown id gets, so this does not become a
way to learn which ids belong to pending or disabled accounts.
"""

import base64
import uuid

import pytest

from app import app as flask_app, socketio

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


@pytest.fixture
def world(store):
    _received.clear()
    alice, _ = store.create_user('alice', 'correct-horse-1')
    bob, _ = store.create_user('bob', 'correct-horse-2')
    pending, _ = store.create_user('pending1', 'correct-horse-3')
    disabled, _ = store.create_user('disabled1', 'correct-horse-4')
    store.set_account_state(pending['id'], 'pending')
    store.set_account_state(disabled['id'], 'disabled')
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'alice', 'password': 'correct-horse-1'})
    assert events(sock, 'auth_success')
    yield sock, alice, bob, pending, disabled
    sock.disconnect()


def sends(recipient_id):
    return [
        ('send_message', {'recipient_id': recipient_id, 'message': 'hello'}),
        ('send_message', {'recipient_id': recipient_id, 'message': 'Y2lwaGVy', 'iv': 'aXY=',
                          'encrypted': True, 'v': 2, 'msg_uuid': str(uuid.uuid4())}),
        ('send_voice', {'recipient_id': recipient_id, 'audio': base64.b64encode(b'\x01' * 64).decode(),
                        'iv': 'aXY=', 'mime': 'audio/webm', 'encrypted': True, 'v': 2,
                        'msg_uuid': str(uuid.uuid4())}),
    ]


@pytest.mark.parametrize('who', ['pending', 'disabled'])
def test_no_message_reaches_an_inactive_account(world, store, who):
    sock, alice, bob, pending, disabled = world
    target = pending if who == 'pending' else disabled
    for event, payload in sends(target['id']):
        sock.emit(event, payload)
    assert store.get_conversation(alice['id'], target['id']) == []


def test_messages_to_active_accounts_still_arrive(world, store):
    sock, alice, bob, pending, disabled = world
    for event, payload in sends(bob['id']):
        sock.emit(event, payload)
    assert len(store.get_conversation(alice['id'], bob['id'])) == 3


def test_the_storage_layer_refuses_too(world, store):
    """Defence in depth: a future handler that forgets the check still cannot
    write a row for an inactive account."""
    sock, alice, bob, pending, disabled = world
    assert store.add_message(alice['id'], disabled['id'], 'text', 'x') is None
    assert store.add_message(alice['id'], bob['id'], 'text', 'x') is not None


def test_forwarding_to_an_inactive_account_is_refused(world, store):
    sock, alice, bob, pending, disabled = world
    sock.emit('send_message', {'recipient_id': bob['id'], 'message': 'forward me'})
    message_id = store.get_conversation(alice['id'], bob['id'])[-1]['id']
    sock.emit('forward_message', {'message_id': message_id, 'recipient_id': disabled['id']})
    assert store.get_conversation(alice['id'], disabled['id']) == []


def test_an_inactive_account_looks_like_an_unknown_one(world, store):
    sock, alice, bob, pending, disabled = world
    sock.emit('send_message', {'recipient_id': 'no-such-account', 'message': 'hello'})
    unknown = [e['args'] for e in sock.get_received()]
    _received.clear()
    sock.emit('send_message', {'recipient_id': disabled['id'], 'message': 'hello'})
    inactive = [e['args'] for e in sock.get_received()]
    assert unknown == inactive


@pytest.mark.parametrize('who', ['pending', 'disabled'])
def test_inactive_accounts_cannot_be_put_in_a_group(world, store, who):
    sock, alice, bob, pending, disabled = world
    target = pending if who == 'pending' else disabled
    group, error = store.create_group(alice['id'], 'team', '', [bob['id'], target['id']])
    assert error is None
    members = set(store.get_group_member_ids(group['id']))
    assert target['id'] not in members and bob['id'] in members

    added, _ = store.add_group_members(group['id'], [target['id']])
    assert added == []
    assert target['id'] not in set(store.get_group_member_ids(group['id']))


def test_a_member_disabled_later_gets_no_new_group_messages(world, store):
    sock, alice, bob, pending, disabled = world
    group, _ = store.create_group(alice['id'], 'team', '', [bob['id']])
    store.set_account_state(bob['id'], 'disabled')
    store.add_group_message(
        alice['id'], group['id'], 'text',
        {alice['id']: {'content': 'a', 'iv': ''}, bob['id']: {'content': 'b', 'iv': ''}}, {})
    with store._connect() as db:
        rows = db.execute("SELECT recipient_id FROM messages WHERE group_id = ?",
                          (group['id'],)).fetchall()
    recipients = {r['recipient_id'] for r in rows}
    assert alice['id'] in recipients, 'the fan-out did not run at all'
    assert bob['id'] not in recipients
