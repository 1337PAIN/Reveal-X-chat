"""Administration: the bootstrap admin, the approval gate, and the powers.

The point of most of these is not that the happy path works -- it is that the
gate cannot be walked around. An admin panel whose authorisation lives in the
client is decoration, so the tests that matter here are the ones asserting a
normal user's socket achieves nothing when it emits an admin event.
"""

import pytest
from flask_socketio import SocketIOTestClient

from app import app as flask_app, socketio

_received = {}


def events(client, name):
    """get_received() is destructive, so accumulate rather than re-read."""
    _received.setdefault(client, []).extend(client.get_received())
    return [item for item in _received[client] if item['name'] == name]


def sign_in(store, username, password, **kwargs):
    """Create an approved account and return a signed-in socket for it."""
    user, error = store.create_user(username, password, **kwargs)
    assert error is None, error
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': username, 'password': password})
    return sock, user


@pytest.fixture(autouse=True)
def _clear_events():
    _received.clear()
    yield
    _received.clear()


@pytest.fixture
def admin_sock(store):
    sock, user = sign_in(store, 'root', 'correct-horse-admin', role='admin')
    assert events(sock, 'auth_success'), 'admin could not sign in'
    yield sock, user
    sock.disconnect()


# ----------------------------------------------------------------------
# Bootstrap
# ----------------------------------------------------------------------

def test_ensure_admin_creates_the_superadmin(store):
    user, created, error = store.ensure_admin('root', 'correct-horse-admin')
    assert error is None and created is True
    assert user['role'] == 'superadmin'
    assert user['account_state'] == 'active'


def test_ensure_admin_is_idempotent_across_restarts(store):
    store.ensure_admin('root', 'correct-horse-admin')
    user, created, error = store.ensure_admin('root', 'correct-horse-admin')
    assert error is None and created is False
    assert store.count_admins() == 1


def test_ensure_admin_does_not_reset_an_existing_password(store):
    """Otherwise a stale env var silently reverts a password the admin changed,
    and anyone able to set one could take over the account."""
    store.create_user('root', 'the-real-password', role='admin')
    store.ensure_admin('root', 'attacker-supplied-password')

    assert store.authenticate('root', 'attacker-supplied-password')[0] is None
    assert store.authenticate('root', 'the-real-password')[0] is not None


def test_ensure_admin_promotes_and_reactivates_an_existing_account(store):
    user, _ = store.create_user('root', 'correct-horse-admin')
    store.set_account_state(user['id'], 'disabled')

    store.ensure_admin('root', 'ignored')

    account = [a for a in store.list_accounts() if a['id'] == user['id']][0]
    assert account['role'] == 'superadmin'
    assert account['account_state'] == 'active'


# ----------------------------------------------------------------------
# The approval gate
# ----------------------------------------------------------------------

def test_registration_creates_a_pending_account_and_no_session(store):
    sock = socketio.test_client(flask_app)
    sock.emit('register', {'username': 'newcomer', 'password': 'correct-horse-1'})

    assert events(sock, 'registration_submitted')
    assert events(sock, 'auth_success') == []

    accounts = store.list_accounts()
    assert [a['account_state'] for a in accounts] == ['pending']
    sock.disconnect()


def test_a_pending_account_cannot_sign_in(store):
    store.create_user('newcomer', 'correct-horse-1', account_state='pending')
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'newcomer', 'password': 'correct-horse-1'})

    assert events(sock, 'auth_success') == []
    assert 'approval' in events(sock, 'auth_error')[0]['args'][0]['error']
    sock.disconnect()


def test_a_disabled_account_cannot_sign_in(store):
    user, _ = store.create_user('gone', 'correct-horse-1')
    store.set_account_state(user['id'], 'disabled')

    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'gone', 'password': 'correct-horse-1'})
    assert events(sock, 'auth_success') == []
    assert 'disabled' in events(sock, 'auth_error')[0]['args'][0]['error']
    sock.disconnect()


def test_the_wrong_password_is_reported_before_the_account_state(store):
    """Saying 'awaiting approval' to someone with the wrong password would
    confirm the username exists."""
    store.create_user('newcomer', 'correct-horse-1', account_state='pending')
    user, error = store.authenticate('newcomer', 'WRONG')
    assert user is None
    assert error == 'Invalid username or password'


def test_registration_does_not_reveal_that_a_username_is_taken(store):
    """Requests are invisible to the requester, so an honest 'already exists'
    would turn this form into a username oracle."""
    store.create_user('taken', 'correct-horse-1')

    sock = socketio.test_client(flask_app)
    sock.emit('register', {'username': 'taken', 'password': 'different-pass-9'})
    assert events(sock, 'registration_submitted')
    assert events(sock, 'auth_error') == []
    sock.disconnect()


def test_pending_and_disabled_accounts_are_not_chat_partners(store):
    active, _ = store.create_user('active_one', 'correct-horse-1')
    store.create_user('waiting', 'correct-horse-2', account_state='pending')
    blocked, _ = store.create_user('blocked', 'correct-horse-3')
    store.set_account_state(blocked['id'], 'disabled')

    names = [u['username'] for u in store.get_users_list()]
    assert 'active_one' in names
    assert 'waiting' not in names
    assert 'blocked' not in names


def test_approval_lets_the_account_in(store, admin_sock):
    sock, _ = admin_sock
    pending, _ = store.create_user('newcomer', 'correct-horse-1', account_state='pending')

    sock.emit('admin_set_state', {'user_id': pending['id'], 'state': 'active'})
    assert events(sock, 'admin_ok')

    user_sock = socketio.test_client(flask_app)
    user_sock.emit('login', {'username': 'newcomer', 'password': 'correct-horse-1'})
    assert events(user_sock, 'auth_success')
    user_sock.disconnect()


# ----------------------------------------------------------------------
# Authorisation -- the part that actually matters
# ----------------------------------------------------------------------

ADMIN_EVENTS = [
    ('admin_list_accounts', None),
    ('admin_set_state', {'user_id': 'x', 'state': 'active'}),
    ('admin_create_account', {'username': 'sneaky', 'password': 'correct-horse-9'}),
    ('admin_reset_password', {'user_id': 'x', 'password': 'correct-horse-9'}),
    ('admin_delete_account', {'user_id': 'x'}),
]


@pytest.mark.parametrize('event,payload', ADMIN_EVENTS)
def test_a_normal_user_cannot_use_any_admin_event(store, event, payload):
    sock, _ = sign_in(store, 'plain', 'correct-horse-1')
    assert events(sock, 'auth_success')

    sock.emit(event, payload) if payload is not None else sock.emit(event)

    assert events(sock, 'admin_error'), f'{event} was not refused'
    assert events(sock, 'admin_ok') == []
    assert events(sock, 'admin_accounts') == []
    sock.disconnect()


@pytest.mark.parametrize('event,payload', ADMIN_EVENTS)
def test_an_anonymous_socket_cannot_use_any_admin_event(store, event, payload):
    sock = socketio.test_client(flask_app)
    sock.emit(event, payload) if payload is not None else sock.emit(event)

    assert events(sock, 'admin_error')
    assert events(sock, 'admin_accounts') == []
    sock.disconnect()


def test_a_normal_user_cannot_create_an_account_by_asking(store):
    """The concrete damage the previous test prevents."""
    sock, _ = sign_in(store, 'plain', 'correct-horse-1')
    sock.emit('admin_create_account', {'username': 'sneaky', 'password': 'correct-horse-9'})

    assert [a['username'] for a in store.list_accounts()] == ['plain']
    sock.disconnect()


def test_demotion_takes_effect_without_a_re_login(store, admin_sock):
    """The role is re-read per call, not trusted from the cached session."""
    sock, admin = admin_sock
    store.create_user('other_admin', 'correct-horse-2', role='admin')

    # Drain the roster this socket legitimately received while it was still
    # an admin, so the assertion below is about what happens *after* demotion.
    sock.get_received()
    _received.pop(sock, None)

    # Demote the signed-in admin behind its own back.
    with store._connect() as db:
        db.execute("UPDATE users SET role = 'user' WHERE id = ?", (admin['id'],))

    sock.emit('admin_list_accounts')
    assert events(sock, 'admin_error')
    assert events(sock, 'admin_accounts') == []


# ----------------------------------------------------------------------
# Powers
# ----------------------------------------------------------------------

def test_admin_creates_an_account_that_can_sign_in_immediately(store, admin_sock):
    sock, _ = admin_sock
    sock.emit('admin_create_account', {'username': 'created', 'password': 'correct-horse-4'})
    assert events(sock, 'admin_ok')

    user_sock = socketio.test_client(flask_app)
    user_sock.emit('login', {'username': 'created', 'password': 'correct-horse-4'})
    assert events(user_sock, 'auth_success')
    user_sock.disconnect()


def test_admin_created_accounts_still_obey_password_rules(store, admin_sock):
    sock, _ = admin_sock
    sock.emit('admin_create_account', {'username': 'weak', 'password': 'short'})
    assert events(sock, 'admin_error')
    assert [a['username'] for a in store.list_accounts()] == ['root']


def test_disabling_kicks_the_live_session(store, admin_sock):
    """Otherwise the account is 'disabled' but keeps chatting until it reloads."""
    sock, _ = admin_sock
    victim_sock, victim = sign_in(store, 'victim', 'correct-horse-5')
    assert events(victim_sock, 'auth_success')

    sock.emit('admin_set_state', {'user_id': victim['id'], 'state': 'disabled'})

    assert events(victim_sock, 'force_signed_out')
    assert store.get_session_user(victim_sock.eio_sid) is None
    victim_sock.disconnect()


def test_resetting_a_password_kicks_the_live_session(store, admin_sock):
    """A reset exists to lock someone out; leaving their socket alive defeats it."""
    sock, _ = admin_sock
    victim_sock, victim = sign_in(store, 'victim', 'correct-horse-5')
    assert events(victim_sock, 'auth_success')

    sock.emit('admin_reset_password', {'user_id': victim['id'], 'password': 'brand-new-pass-1'})
    assert events(sock, 'admin_ok')
    assert events(victim_sock, 'force_signed_out')

    assert store.authenticate('victim', 'correct-horse-5')[0] is None
    assert store.authenticate('victim', 'brand-new-pass-1')[0] is not None
    victim_sock.disconnect()


def test_reset_rejects_a_weak_password(store, admin_sock):
    sock, _ = admin_sock
    victim, _ = store.create_user('victim', 'correct-horse-5')

    sock.emit('admin_reset_password', {'user_id': victim['id'], 'password': 'short'})
    assert events(sock, 'admin_error')
    assert store.authenticate('victim', 'correct-horse-5')[0] is not None


def test_deleting_an_account_takes_its_messages_with_it(store, admin_sock):
    sock, _ = admin_sock
    victim, _ = store.create_user('victim', 'correct-horse-5')
    other, _ = store.create_user('other', 'correct-horse-6')
    store.add_message(victim['id'], other['id'], 'text', 'hello')

    sock.emit('admin_delete_account', {'user_id': victim['id']})
    assert events(sock, 'admin_ok')

    assert store.get_user(victim['id']) is None
    assert store.get_conversation(other['id'], victim['id']) == []


# ----------------------------------------------------------------------
# Lockout guards
# ----------------------------------------------------------------------

def test_admin_cannot_delete_their_own_account(store, admin_sock):
    sock, admin = admin_sock
    sock.emit('admin_delete_account', {'user_id': admin['id']})

    assert events(sock, 'admin_error')
    assert store.get_user(admin['id']) is not None


def test_admin_cannot_disable_their_own_account(store, admin_sock):
    sock, admin = admin_sock
    sock.emit('admin_set_state', {'user_id': admin['id'], 'state': 'disabled'})

    assert events(sock, 'admin_error')
    assert store.count_admins() == 1


def test_an_admin_cannot_remove_a_peer_admin(store):
    """Rank must be strictly greater, so admins cannot fight each other."""
    sock, _ = sign_in(store, 'first', 'correct-horse-6', role='admin')
    assert events(sock, 'auth_success')
    peer, _ = store.create_user('second', 'correct-horse-7', role='admin')

    sock.emit('admin_delete_account', {'user_id': peer['id']})
    assert events(sock, 'admin_error')
    assert store.get_user(peer['id']) is not None
    sock.disconnect()


def test_count_admins_ignores_disabled_ones(store):
    store.create_user('admin_one', 'correct-horse-1', role='admin')
    disabled, _ = store.create_user('admin_two', 'correct-horse-2', role='admin')
    store.set_account_state(disabled['id'], 'disabled')

    assert store.count_admins() == 1


# ----------------------------------------------------------------------
# Roster visibility
# ----------------------------------------------------------------------

def test_the_admin_is_not_a_contact_for_normal_users(store):
    """The admin is a management account, not somebody to chat to.

    Leaving it in the roster meant every user could see it and watch it come
    online, which is noise at best.
    """
    admin, _ = store.create_user('root', 'correct-horse-admin', role='admin')
    alice, _ = store.create_user('alice', 'correct-horse-1')
    store.create_user('bob', 'correct-horse-2')

    names = [u['username'] for u in store.get_users_list(alice['id'])]
    assert names == ['bob']
    assert 'root' not in names


def test_an_admin_still_sees_everyone(store):
    admin, _ = store.create_user('root', 'correct-horse-admin', role='admin')
    store.create_user('alice', 'correct-horse-1')
    store.create_user('bob', 'correct-horse-2')

    names = sorted(u['username'] for u in store.get_users_list(admin['id']))
    assert names == ['alice', 'bob']


def test_a_second_admin_is_hidden_from_users_too(store):
    """The rule is about the role, not about which account bootstrapped."""
    store.create_user('root', 'correct-horse-admin', role='admin')
    second, _ = store.create_user('deputy', 'correct-horse-3', role='admin')
    alice, _ = store.create_user('alice', 'correct-horse-1')

    names = [u['username'] for u in store.get_users_list(alice['id'])]
    assert names == []


def test_demoting_an_admin_puts_them_back_in_the_roster(store):
    admin, _ = store.create_user('root', 'correct-horse-admin', role='admin')
    store.create_user('deputy', 'correct-horse-3', role='admin')
    alice, _ = store.create_user('alice', 'correct-horse-1')

    with store._connect() as db:
        db.execute("UPDATE users SET role = 'user' WHERE username = 'deputy'")

    names = [u['username'] for u in store.get_users_list(alice['id'])]
    assert names == ['deputy']


# ----------------------------------------------------------------------
# Superadmin hierarchy
#
# The rule is one line -- an account may only act on one of strictly lower
# rank -- but it has to hold from every direction, so each of these probes a
# different way round it.
# ----------------------------------------------------------------------

@pytest.fixture
def super_sock(store):
    store.ensure_admin('root', 'correct-horse-super')
    sock = socketio.test_client(flask_app)
    sock.emit('login', {'username': 'root', 'password': 'correct-horse-super'})
    user = events(sock, 'auth_success')[0]['args'][0]['user']
    assert user['role'] == 'superadmin'
    yield sock, user
    sock.disconnect()


def test_rank_ordering(store):
    assert store.role_rank('user') < store.role_rank('admin') < store.role_rank('superadmin')


def test_an_admin_cannot_touch_the_superadmin(store):
    """The whole point of the rank: root is out of reach of its own deputies."""
    root, _, _ = store.ensure_admin('root', 'correct-horse-super')
    sock, _ = sign_in(store, 'deputy', 'correct-horse-8', role='admin')
    assert events(sock, 'auth_success')

    for event, payload in [
        ('admin_set_state', {'user_id': root['id'], 'state': 'disabled'}),
        ('admin_delete_account', {'user_id': root['id']}),
        ('admin_reset_password', {'user_id': root['id'], 'password': 'hijacked-pass-1'}),
        ('admin_set_role', {'user_id': root['id'], 'role': 'user'}),
    ]:
        sock.emit(event, payload)

    assert events(sock, 'admin_ok') == []
    account = [a for a in store.list_accounts() if a['id'] == root['id']][0]
    assert account['role'] == 'superadmin'
    assert account['account_state'] == 'active'
    assert store.authenticate('root', 'correct-horse-super')[0] is not None
    sock.disconnect()


def test_an_admin_cannot_promote_anyone(store):
    """Otherwise an admin promotes a friend and the hierarchy is decorative."""
    store.ensure_admin('root', 'correct-horse-super')
    sock, _ = sign_in(store, 'deputy', 'correct-horse-8', role='admin')
    victim, _ = store.create_user('plain', 'correct-horse-1')

    sock.emit('admin_set_role', {'user_id': victim['id'], 'role': 'admin'})
    assert events(sock, 'admin_error')
    assert store.get_role(victim['id']) == 'user'
    sock.disconnect()


def test_an_admin_cannot_create_another_admin(store):
    store.ensure_admin('root', 'correct-horse-super')
    sock, _ = sign_in(store, 'deputy', 'correct-horse-8', role='admin')

    sock.emit('admin_create_account',
              {'username': 'ally', 'password': 'correct-horse-9', 'make_admin': True})
    assert events(sock, 'admin_error')
    assert store.get_user  # created nothing with the admin role
    assert [a['username'] for a in store.list_accounts() if a['role'] == 'admin'] == ['deputy']
    sock.disconnect()


def test_the_superadmin_can_promote_and_demote(store, super_sock):
    sock, _ = super_sock
    plain, _ = store.create_user('plain', 'correct-horse-1')

    sock.emit('admin_set_role', {'user_id': plain['id'], 'role': 'admin'})
    assert events(sock, 'admin_ok')
    assert store.get_role(plain['id']) == 'admin'

    sock.emit('admin_set_role', {'user_id': plain['id'], 'role': 'user'})
    assert store.get_role(plain['id']) == 'user'


def test_the_superadmin_can_manage_an_admin(store, super_sock):
    sock, _ = super_sock
    deputy, _ = store.create_user('deputy', 'correct-horse-8', role='admin')

    sock.emit('admin_set_state', {'user_id': deputy['id'], 'state': 'disabled'})
    assert events(sock, 'admin_ok')
    account = [a for a in store.list_accounts() if a['id'] == deputy['id']][0]
    assert account['account_state'] == 'disabled'


def test_nobody_can_be_granted_the_superadmin_rank_through_the_ui(store, super_sock):
    """That rank comes from the environment at startup and nowhere else."""
    sock, _ = super_sock
    plain, _ = store.create_user('plain', 'correct-horse-1')

    sock.emit('admin_set_role', {'user_id': plain['id'], 'role': 'superadmin'})
    assert events(sock, 'admin_error')
    assert store.get_role(plain['id']) == 'user'


def test_the_superadmin_cannot_disable_or_demote_itself(store, super_sock):
    """Either would strand the deployment without its top-level account."""
    sock, root = super_sock

    sock.emit('admin_set_state', {'user_id': root['id'], 'state': 'disabled'})
    sock.emit('admin_set_role', {'user_id': root['id'], 'role': 'user'})
    sock.emit('admin_delete_account', {'user_id': root['id']})

    assert events(sock, 'admin_ok') == []
    assert store.get_role(root['id']) == 'superadmin'
    assert store.count_admins() >= 1


def test_there_is_only_ever_one_superadmin(store):
    """Pointing the env var at a different account moves the rank, not adds to it."""
    store.ensure_admin('root', 'correct-horse-super')
    store.ensure_admin('newroot', 'correct-horse-super2')

    roles = {a['username']: a['role'] for a in store.list_accounts()}
    assert roles['newroot'] == 'superadmin'
    assert roles['root'] == 'admin'


def test_superadmin_is_hidden_from_normal_users(store):
    store.ensure_admin('root', 'correct-horse-super')
    alice, _ = store.create_user('alice', 'correct-horse-1')
    store.create_user('bob', 'correct-horse-2')

    assert [u['username'] for u in store.get_users_list(alice['id'])] == ['bob']
