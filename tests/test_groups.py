"""Group conversations, and who is allowed to do what inside one.

Reveal-X already had a server-wide rank model: user < admin < superadmin, and
an account may act only on one strictly below its own. A group needs the same
shape on a different axis -- member < admin < owner, scoped to one group --
and the two must not be confused. A server administrator has no standing
inside a group they are not in, and a group owner gets no server privileges.

The interface hides what a member may not do. These tests drive the socket
directly, because hiding a button is not a control: every refusal below has to
hold against a client that simply sends the event anyway.
"""

import pytest

from app import app as flask_app, socketio

_received = {}


def events(client, name):
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def last(client, name):
    got = events(client, name)
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
def crew(store):
    """An owner, two members and an outsider who is in no group at all."""
    o, owner = sign_in(store, 'owner', 'correct-horse-1')
    a, alice = sign_in(store, 'alice', 'correct-horse-2')
    b, bob = sign_in(store, 'bob', 'correct-horse-3')
    x, mallory = sign_in(store, 'mallory', 'correct-horse-4')
    _received.clear()
    for sock in (o, a, b, x):
        sock.get_received()
    yield (o, owner), (a, alice), (b, bob), (x, mallory)
    for sock in (o, a, b, x):
        sock.disconnect()


def make_group(sock, name='Study Group', members=()):
    sock.emit('group_create', {
        'name': name,
        'description': 'FYP team',
        'member_ids': list(members),
    })
    created = last(sock, 'group_created')
    return created['group'] if created else None


# ----------------------------------------------------------------- creation

def test_creating_a_group_makes_the_creator_its_owner(crew, store):
    (o, owner), (a, alice), (b, bob), _ = crew
    group = make_group(o, members=[alice['id'], bob['id']])

    assert group['name'] == 'Study Group'
    assert group['member_count'] == 3
    assert group['role'] == 'owner'
    roles = {m['username']: m['role'] for m in store.get_group_members(group['id'])}
    assert roles == {'owner': 'owner', 'alice': 'member', 'bob': 'member'}


def test_a_group_needs_a_name(crew):
    (o, _owner), *_ = crew
    o.emit('group_create', {'name': '   ', 'member_ids': []})
    assert last(o, 'group_error'), 'an unnamed group was created'


def test_members_are_told_about_a_group_they_were_added_to(crew):
    (o, _owner), (a, alice), _, _ = crew
    make_group(o, members=[alice['id']])
    assert last(a, 'group_updated'), 'alice was never told she is in a group'


# ------------------------------------------------------------- membership

def test_an_outsider_cannot_open_a_group(crew):
    (o, _owner), (a, alice), _, (x, _mallory) = crew
    group = make_group(o, members=[alice['id']])

    x.emit('group_open', {'group_id': group['id']})

    assert last(x, 'group_error'), 'an outsider was allowed to open the group'
    assert not events(x, 'group_opened')


def test_a_plain_member_cannot_add_or_remove_anyone(crew, store):
    (o, _owner), (a, alice), (b, bob), (x, mallory) = crew
    group = make_group(o, members=[alice['id'], bob['id']])

    a.emit('group_add_members', {'group_id': group['id'], 'user_ids': [mallory['id']]})
    assert last(a, 'group_error')
    assert mallory['id'] not in store.get_group_member_ids(group['id'])

    a.emit('group_remove_member', {'group_id': group['id'], 'user_id': bob['id']})
    assert bob['id'] in store.get_group_member_ids(group['id']), 'a member removed a member'


def test_an_admin_can_remove_a_member(crew, store):
    (o, _owner), (a, alice), (b, bob), _ = crew
    group = make_group(o, members=[alice['id'], bob['id']])
    o.emit('group_set_role', {'group_id': group['id'], 'user_id': alice['id'],
                              'role': 'admin'})

    a.emit('group_remove_member', {'group_id': group['id'], 'user_id': bob['id']})

    assert bob['id'] not in store.get_group_member_ids(group['id'])
    assert last(b, 'group_removed'), 'bob was not told he was removed'


def test_an_admin_cannot_remove_a_peer_admin_or_the_owner(crew, store):
    (o, owner), (a, alice), (b, bob), _ = crew
    group = make_group(o, members=[alice['id'], bob['id']])
    for who in (alice, bob):
        o.emit('group_set_role', {'group_id': group['id'], 'user_id': who['id'],
                                  'role': 'admin'})

    a.emit('group_remove_member', {'group_id': group['id'], 'user_id': bob['id']})
    assert bob['id'] in store.get_group_member_ids(group['id']), 'an admin removed a peer'

    a.emit('group_remove_member', {'group_id': group['id'], 'user_id': owner['id']})
    assert owner['id'] in store.get_group_member_ids(group['id']), 'an admin removed the owner'


def test_nobody_can_remove_themselves_through_the_admin_route(crew, store):
    """Leaving is its own event, which hands the group on. Self-removal here
    would strand an ownerless group."""
    (o, owner), (a, alice), _, _ = crew
    group = make_group(o, members=[alice['id']])

    o.emit('group_remove_member', {'group_id': group['id'], 'user_id': owner['id']})

    assert owner['id'] in store.get_group_member_ids(group['id'])
    assert last(o, 'group_error')


def test_the_owner_cannot_be_demoted(crew, store):
    (o, owner), (a, alice), _, _ = crew
    group = make_group(o, members=[alice['id']])
    o.emit('group_set_role', {'group_id': group['id'], 'user_id': alice['id'],
                              'role': 'admin'})

    a.emit('group_set_role', {'group_id': group['id'], 'user_id': owner['id'],
                              'role': 'member'})

    assert store.group_role(group['id'], owner['id']) == 'owner'


def test_a_server_administrator_has_no_standing_in_a_group(crew, store):
    """The two ladders are separate. Being an administrator of the deployment
    is not membership of a private conversation."""
    (o, _owner), (a, alice), _, (x, mallory) = crew
    store.set_role(mallory['id'], 'admin')
    group = make_group(o, members=[alice['id']])

    x.emit('group_open', {'group_id': group['id']})
    x.emit('group_remove_member', {'group_id': group['id'], 'user_id': alice['id']})

    assert not events(x, 'group_opened')
    assert alice['id'] in store.get_group_member_ids(group['id'])


# ------------------------------------------------------------- messaging

def test_a_message_is_fanned_out_one_copy_per_member(crew, store):
    """Reveal-X encrypts pairwise, so a group message is encrypted once per
    member. Each member must receive their own copy and no one else's."""
    (o, owner), (a, alice), (b, bob), _ = crew
    group = make_group(o, members=[alice['id'], bob['id']])

    o.emit('group_send', {
        'group_id': group['id'],
        'encrypted': True,
        'payloads': {
            owner['id']: {'content': 'cipher-for-owner', 'iv': 'iv1'},
            alice['id']: {'content': 'cipher-for-alice', 'iv': 'iv2'},
            bob['id']: {'content': 'cipher-for-bob', 'iv': 'iv3'},
        },
    })

    assert last(a, 'new_group_message')['content'] == 'cipher-for-alice'
    assert last(b, 'new_group_message')['content'] == 'cipher-for-bob'
    assert last(o, 'new_group_message')['content'] == 'cipher-for-owner'


def test_a_payload_aimed_at_a_non_member_is_dropped(crew, store):
    (o, owner), (a, alice), _, (x, mallory) = crew
    group = make_group(o, members=[alice['id']])

    o.emit('group_send', {
        'group_id': group['id'],
        'encrypted': True,
        'payloads': {
            owner['id']: {'content': 'c1', 'iv': 'i'},
            alice['id']: {'content': 'c2', 'iv': 'i'},
            mallory['id']: {'content': 'smuggled', 'iv': 'i'},
        },
    })

    assert not events(x, 'new_group_message'), 'a non-member was delivered a copy'
    assert store.get_group_conversation(group['id'], mallory['id']) == []


def test_an_outsider_cannot_post(crew):
    (o, _owner), (a, alice), _, (x, mallory) = crew
    group = make_group(o, members=[alice['id']])

    x.emit('group_send', {'group_id': group['id'], 'encrypted': True,
                          'payloads': {mallory['id']: {'content': 'hello', 'iv': 'i'}}})

    assert last(x, 'group_error')
    assert not events(a, 'new_group_message')


def test_only_admins_can_post_when_the_group_says_so(crew):
    (o, owner), (a, alice), _, _ = crew
    group = make_group(o, members=[alice['id']])
    o.emit('group_update_settings', {'group_id': group['id'], 'only_admins_post': True})

    a.emit('group_send', {'group_id': group['id'], 'encrypted': True,
                          'payloads': {alice['id']: {'content': 'hi', 'iv': 'i'}}})
    assert last(a, 'group_error'), 'a member posted to an admins-only group'

    o.emit('group_send', {'group_id': group['id'], 'encrypted': True,
                          'payloads': {owner['id']: {'content': 'hi', 'iv': 'i'},
                                       alice['id']: {'content': 'hi', 'iv': 'i'}}})
    assert last(a, 'new_group_message'), 'an admin could not post either'


def test_images_are_refused_in_groups(crew, store):
    """Share 2 is delivered live to one recipient and never stored, which
    cannot hold for a group whose members are offline."""
    (o, owner), (a, alice), _, _ = crew
    group = make_group(o, members=[alice['id']])

    fanned = store.add_group_message(
        owner['id'], group['id'], 'image',
        {owner['id']: {'content': 'x', 'iv': 'i'}}, {})

    assert fanned is None


def test_a_removed_member_receives_nothing_further(crew, store):
    (o, owner), (a, alice), (b, bob), _ = crew
    group = make_group(o, members=[alice['id'], bob['id']])
    o.emit('group_remove_member', {'group_id': group['id'], 'user_id': bob['id']})
    b.get_received()

    o.emit('group_send', {
        'group_id': group['id'],
        'encrypted': True,
        'payloads': {
            owner['id']: {'content': 'c1', 'iv': 'i'},
            alice['id']: {'content': 'c2', 'iv': 'i'},
            bob['id']: {'content': 'should-not-arrive', 'iv': 'i'},
        },
    })

    assert not events(b, 'new_group_message'), 'a removed member still receives messages'
    assert store.get_group_conversation(group['id'], bob['id']) == []


# ---------------------------------------------------------------- settings

def test_only_admins_can_edit_the_group_info_by_default(crew, store):
    (o, _owner), (a, alice), _, _ = crew
    group = make_group(o, members=[alice['id']])

    a.emit('group_update_info', {'group_id': group['id'], 'name': 'Renamed by a member'})

    assert last(a, 'group_error')
    assert store.get_group(group['id'])['name'] == 'Study Group'


def test_members_may_edit_the_info_when_the_group_allows_it(crew, store):
    (o, _owner), (a, alice), _, _ = crew
    group = make_group(o, members=[alice['id']])
    o.emit('group_update_settings', {'group_id': group['id'],
                                     'only_admins_edit_info': False})

    a.emit('group_update_info', {'group_id': group['id'], 'name': 'Renamed'})

    assert store.get_group(group['id'])['name'] == 'Renamed'


def test_a_member_cannot_change_the_group_settings(crew, store):
    (o, _owner), (a, alice), _, _ = crew
    group = make_group(o, members=[alice['id']])

    a.emit('group_update_settings', {'group_id': group['id'], 'only_admins_post': True})

    assert last(a, 'group_error')
    assert store.get_group(group['id'])['only_admins_post'] is False


# ------------------------------------------------------------------ leaving

def test_an_owner_who_leaves_hands_the_group_to_an_admin(crew, store):
    (o, owner), (a, alice), (b, bob), _ = crew
    group = make_group(o, members=[alice['id'], bob['id']])
    o.emit('group_set_role', {'group_id': group['id'], 'user_id': alice['id'],
                              'role': 'admin'})

    o.emit('group_leave', {'group_id': group['id']})

    assert owner['id'] not in store.get_group_member_ids(group['id'])
    assert store.group_role(group['id'], alice['id']) == 'owner', (
        'the group was left without an owner'
    )


def test_the_last_member_leaving_deletes_the_group(crew, store):
    (o, _owner), _, _, _ = crew
    group = make_group(o, members=[])

    o.emit('group_leave', {'group_id': group['id']})

    assert store.get_group(group['id']) is None


def test_deleting_an_account_does_not_strand_the_groups_it_owned(crew, store):
    (o, owner), (a, alice), _, _ = crew
    group = make_group(o, members=[alice['id']])

    store.purge_user(owner['id'])

    assert owner['id'] not in store.get_group_member_ids(group['id'])
    assert store.group_role(group['id'], alice['id']) == 'owner'
