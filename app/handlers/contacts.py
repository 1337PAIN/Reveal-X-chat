"""Contacts: add someone by username, accept or decline, remove.

Every account used to see and message every other. Now the roster is your
contacts, a contact is someone who accepted your request (or whose request
you accepted), and every send path refuses anyone else (NOT_A_CONTACT in
common.py; the storage layer refuses too). See ChatRoom's contacts section.
"""

from app.handlers.common import *  # noqa: F401,F403


def emit_contacts(user_id):
    """Send this account's pending requests to each of its sessions."""
    payload = chat_room.contact_requests(user_id)
    for session_id in chat_room.get_sessions_for_user(user_id):
        emit('contacts_updated', payload, to=session_id)


def _refresh(*user_ids):
    """Both people's requests changed, and maybe their rosters."""
    for user_id in user_ids:
        emit_contacts(user_id)
    emit_users()


def emit_roster(user_id):
    """This account's roster, to each of its sessions only."""
    users = chat_room.get_users_list(user_id)
    for session_id in chat_room.get_sessions_for_user(user_id):
        emit('users_updated', {'users': users}, to=session_id)


@socketio.on('chat_pref')
@session_required
def handle_chat_pref(data):
    """Pin, archive or mute a contact's chat, for this account only."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    peer_id = data.get('peer_id')
    if not isinstance(peer_id, str):
        return
    if chat_room.set_chat_pref(current_user['id'], peer_id, data.get('pref'), data.get('value')):
        emit_roster(current_user['id'])


@socketio.on('clear_chat')
@session_required
def handle_clear_chat(data):
    """Hide every message in one conversation from this account."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    peer_id = data.get('peer_id')
    if not isinstance(peer_id, str) or not chat_room.get_user(peer_id):
        return
    chat_room.clear_conversation(current_user['id'], peer_id)
    for session_id in chat_room.get_sessions_for_user(current_user['id']):
        emit('chat_cleared', {'peer_id': peer_id}, to=session_id)


# --- Lock chat ------------------------------------------------------------

def emit_lock_state(user_id):
    pin_set = chat_room.chat_lock_pin_set(user_id)
    for session_id in chat_room.get_sessions_for_user(user_id):
        emit('chat_lock_state', {'pin_set': pin_set, 'open': session_id in CHAT_LOCK_OPEN}, to=session_id)


def _lock_error(message):
    emit('chat_lock_error', {'error': message}, to=request.sid)


@socketio.on('chat_lock_set_pin')
@session_required
def handle_chat_lock_set_pin(data):
    """Set the chat-lock PIN, or change it with the current one.

    `lock_peer_id` locks that chat in the same step. Sent as two events, the
    lock could be handled first -- events run in parallel, and the Argon2id
    hash takes ~100 ms -- and be refused for want of a PIN.
    """
    data = data or {}
    user_id = chat_room.get_session_user(request.sid)['id']
    if chat_room.chat_lock_pin_set(user_id):
        if _chat_lock_wait(user_id):
            _lock_error('Too many wrong PINs. Try again in a few minutes.')
            return
        if not chat_room.verify_chat_lock_pin(user_id, data.get('current_pin')):
            _chat_lock_failed(user_id)
            _lock_error('The current PIN is not right.')
            return
    if not chat_room.set_chat_lock_pin(user_id, data.get('pin')):
        _lock_error('The PIN must be 4 to 8 digits.')
        return
    CHAT_LOCK_OPEN.add(request.sid)
    peer_id = data.get('lock_peer_id')
    if isinstance(peer_id, str) and chat_room.set_chat_locked(user_id, peer_id, True):
        emit_roster(user_id)
    emit_lock_state(user_id)


@socketio.on('chat_lock')
@session_required
def handle_chat_lock(data):
    """Lock one contact's chat, or unlock it (which needs the PIN entered)."""
    data = data or {}
    user_id = chat_room.get_session_user(request.sid)['id']
    peer_id = data.get('peer_id')
    if not isinstance(peer_id, str):
        return
    if not chat_room.chat_lock_pin_set(user_id):
        _lock_error(CHAT_LOCK_NEEDS_PIN)
        return
    locking = bool(data.get('value'))
    if not locking and request.sid not in CHAT_LOCK_OPEN:
        _lock_error('Enter your chat-lock PIN first.')
        return
    if chat_room.set_chat_locked(user_id, peer_id, locking):
        emit_roster(user_id)


def _chat_lock_wait(user_id):
    return state.store().window_wait('chat_lock', [(('user', user_id), CHAT_LOCK_MAX_FAILURES)],
                                     CHAT_LOCK_WINDOW_SECONDS, time.time())


def _chat_lock_failed(user_id):
    state.store().window_reserve('chat_lock', [(('user', user_id), CHAT_LOCK_MAX_FAILURES + 1)],
                                 CHAT_LOCK_WINDOW_SECONDS, time.time())


@socketio.on('chat_lock_open')
@session_required
def handle_chat_lock_open(data):
    """Enter the PIN: this session may open locked chats until it locks again."""
    data = data or {}
    user_id = chat_room.get_session_user(request.sid)['id']
    if _chat_lock_wait(user_id):
        _lock_error('Too many wrong PINs. Try again in a few minutes.')
        emit_lock_state(user_id)
        return
    if not chat_room.verify_chat_lock_pin(user_id, data.get('pin')):
        _chat_lock_failed(user_id)
        _lock_error('That PIN is not right.')
        return
    CHAT_LOCK_OPEN.add(request.sid)
    emit_lock_state(user_id)


@socketio.on('chat_lock_close')
@session_required
def handle_chat_lock_close(_data=None):
    """Hide locked chats again in this session."""
    CHAT_LOCK_OPEN.discard(request.sid)
    emit_lock_state(chat_room.get_session_user(request.sid)['id'])


@socketio.on('chat_lock_reset')
@session_required
def handle_chat_lock_reset(_data=None):
    """Forgotten PIN: clear the locked chats for you, unlock them, drop the PIN."""
    user_id = chat_room.get_session_user(request.sid)['id']
    for peer_id in chat_room.reset_chat_lock(user_id):
        for session_id in chat_room.get_sessions_for_user(user_id):
            emit('chat_cleared', {'peer_id': peer_id}, to=session_id)
    for session_id in chat_room.get_sessions_for_user(user_id):
        CHAT_LOCK_OPEN.discard(session_id)
    emit_lock_state(user_id)
    emit_roster(user_id)


# --- Block ----------------------------------------------------------------

def emit_blocked(user_id):
    blocked = chat_room.blocked_list(user_id)
    for session_id in chat_room.get_sessions_for_user(user_id):
        emit('blocked_updated', {'blocked': blocked}, to=session_id)


@socketio.on('block_user')
@session_required
def handle_block_user(data):
    data = data or {}
    user_id = chat_room.get_session_user(request.sid)['id']
    other_id = data.get('user_id')
    if not isinstance(other_id, str):
        return
    if chat_room.block_user(user_id, other_id):
        emit_blocked(user_id)
        _refresh(user_id, other_id)


@socketio.on('unblock_user')
@session_required
def handle_unblock_user(data):
    data = data or {}
    user_id = chat_room.get_session_user(request.sid)['id']
    other_id = data.get('user_id')
    if not isinstance(other_id, str):
        return
    chat_room.unblock_user(user_id, other_id)
    emit_blocked(user_id)
    emit_contacts(user_id)


@socketio.on('contact_request')
@session_required
def handle_contact_request(data):
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    username = data.get('username')
    if not isinstance(username, str):
        emit('contact_result', {'ok': False, 'error': NO_SUCH_ACCOUNT})
        return
    # Limited like sending: each lookup answers "does this name exist?", so
    # an unlimited field would list the accounts by brute force.
    if not _take_send_token(current_user['id'], 'contact'):
        emit('contact_result', {'ok': False, 'error': SEND_TOO_FAST})
        return

    status, other, error = chat_room.request_contact(current_user['id'], username)
    if error == 'self':
        emit('contact_result', {'ok': False, 'error': 'That is your own username.'})
        return
    if error == 'blocked':
        emit('contact_result', {'ok': False, 'error': YOU_BLOCKED_THEM})
        return
    if error:
        emit('contact_result', {'ok': False, 'error': NO_SUCH_ACCOUNT})
        return

    emit('contact_result', {'ok': True, 'status': status, 'username': other['username']})
    if status in ('requested', 'accepted'):
        _refresh(current_user['id'], other['id'])


@socketio.on('contact_respond')
@session_required
def handle_contact_respond(data):
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    requester_id = data.get('user_id')
    if not isinstance(requester_id, str):
        return
    if chat_room.respond_contact(current_user['id'], requester_id, bool(data.get('accept'))):
        _refresh(current_user['id'], requester_id)


@socketio.on('contact_remove')
@session_required
def handle_contact_remove(data):
    """Remove a contact, or withdraw a request you made."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    other_id = data.get('user_id')
    if not isinstance(other_id, str):
        return
    if chat_room.remove_contact(current_user['id'], other_id):
        _refresh(current_user['id'], other_id)
