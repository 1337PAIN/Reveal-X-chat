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
