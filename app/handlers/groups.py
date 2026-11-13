"""Group conversations.
"""

from app.handlers.common import *  # noqa: F401,F403


# ======================================================================
# Groups
#
# Every handler below re-reads the caller's role from the database. The
# interface hides what a member may not do, but hiding a button is not a
# control: unhiding it in devtools has to buy an attacker nothing, which is
# the same rule the administration panel is held to.
# ======================================================================

MAX_GROUP_PAYLOADS = 64


def _group_actor(group_id):
    """The signed-in caller, if they are a member of this group."""
    user = chat_room.get_session_user(request.sid)
    if not user or not group_id:
        return None
    if not chat_room.is_group_member(group_id, user['id']):
        emit('group_error', {'error': 'You are not in that group'}, to=request.sid)
        return None
    return user


def _group_admin(group_id):
    user = _group_actor(group_id)
    if not user:
        return None
    if chat_room.group_role_rank(chat_room.group_role(group_id, user['id'])) < 1:
        emit('group_error', {'error': 'Only group admins can do that'}, to=request.sid)
        return None
    return user


def _push_group(group_id, exclude_user_id=None):
    """Re-send the group to every member's live sessions."""
    for member_id in chat_room.get_group_member_ids(group_id):
        if member_id == exclude_user_id:
            continue
        payload = {
            'group': chat_room.get_group(group_id, member_id),
            'members': chat_room.get_group_members(group_id),
        }
        for sid in chat_room.get_sessions_for_user(member_id):
            emit('group_updated', payload, to=sid)


def _group_notice(group_id, text, extra_user_ids=()):
    """Announce a membership change to everyone who was in the room for it.

    Deliberately not stored. The message store otherwise holds only
    ciphertext the server cannot read; writing "X removed Y" into it in
    plaintext would put group metadata in the one place this project is
    careful to keep unreadable. The notice is live only.
    """
    targets = set(chat_room.get_group_member_ids(group_id)) | set(extra_user_ids or ())
    for member_id in targets:
        for sid in chat_room.get_sessions_for_user(member_id):
            emit('group_notice', {'group_id': group_id, 'text': text}, to=sid)


@socketio.on('group_create')
@session_required
def handle_group_create(data):
    user = chat_room.get_session_user(request.sid)
    if not user:
        return
    data = data or {}
    members = data.get('member_ids') or []
    if not isinstance(members, list) or len(members) > chat_room.MAX_GROUP_MEMBERS:
        emit('group_error', {'error': 'Too many members'}, to=request.sid)
        return
    group, error = chat_room.create_group(
        user['id'], data.get('name'), data.get('description'), members)
    if error:
        emit('group_error', {'error': error}, to=request.sid)
        return
    _audit(user, 'group_create', None, f"group={group['id']}")
    _push_group(group['id'])
    emit('group_created', {'group': group}, to=request.sid)


@socketio.on('group_list')
@session_required
def handle_group_list():
    user = chat_room.get_session_user(request.sid)
    if not user:
        return
    emit('group_list', {'groups': chat_room.get_groups_for_user(user['id'])},
         to=request.sid)


@socketio.on('group_open')
@session_required
def handle_group_open(data):
    data = data or {}
    group_id = data.get('group_id')
    user = _group_actor(group_id)
    if not user:
        return
    emit('group_opened', {
        'group': chat_room.get_group(group_id, user['id']),
        'members': chat_room.get_group_members(group_id),
        'messages': [
            client_message(m, user['id'])
            for m in chat_room.get_group_conversation(group_id, user['id'])
        ],
    }, to=request.sid)


@socketio.on('group_send')
@session_required
def handle_group_send(data):
    """One ciphertext per member, encrypted client-side under each member's
    pairwise key. The server never sees a group key because there is not one."""
    data = data or {}
    group_id = data.get('group_id')
    user = _group_actor(group_id)
    if not user:
        return
    if not chat_room.may_post_to_group(group_id, user['id']):
        emit('group_error', {'error': 'Only group admins can post here'}, to=request.sid)
        return
    # One group post is one text send, however many members it fans out to.
    if not _take_send_token(user['id'], 'message'):
        emit('group_error', {'error': SEND_TOO_FAST}, to=request.sid)
        return

    payloads = data.get('payloads')
    if not isinstance(payloads, dict) or not payloads:
        return
    if len(payloads) > MAX_GROUP_PAYLOADS:
        emit('group_error', {'error': 'Too many recipients'}, to=request.sid)
        return

    encrypted = bool(data.get('encrypted'))
    limit = MAX_CIPHERTEXT_LENGTH if encrypted else MAX_TEXT_LENGTH
    cleaned = {}
    for member_id, payload in payloads.items():
        if not isinstance(payload, dict):
            payload = {'content': payload}
        content = (payload.get('content') or '').strip()
        iv = payload.get('iv') or ''
        if not content or len(content) > limit or len(iv) > 64:
            continue
        cleaned[member_id] = {'content': content, 'iv': iv}
    if not cleaned:
        return

    extra = {'encrypted': True} if encrypted else {}
    reply_to_id = data.get('reply_to')
    if reply_to_id:
        extra['reply_to'] = reply_preview(
            chat_room.get_message_for_user(reply_to_id, user['id']))

    fanned = chat_room.add_group_message(user['id'], group_id, 'text', cleaned, extra)
    if not fanned:
        return

    for member_id, row_id in fanned['rows'].items():
        message = chat_room.get_message_for_user(row_id, member_id)
        if not message:
            continue
        message['group_id'] = group_id
        message['group_message_id'] = fanned['group_message_id']
        payload = client_message(message, member_id)
        payload['group_id'] = group_id
        payload['group_message_id'] = fanned['group_message_id']
        for sid in chat_room.get_sessions_for_user(member_id):
            emit('new_group_message', payload, to=sid)


@socketio.on('group_add_members')
@session_required
def handle_group_add_members(data):
    data = data or {}
    group_id = data.get('group_id')
    actor = _group_admin(group_id)
    if not actor:
        return
    wanted = data.get('user_ids') or []
    if not isinstance(wanted, list):
        return
    added, error = chat_room.add_group_members(group_id, wanted)
    if error:
        emit('group_error', {'error': error}, to=request.sid)
        return
    if added:
        names = ', '.join(
            (chat_room.get_user(uid) or {}).get('username', 'someone') for uid in added)
        _audit(actor, 'group_add_members', None, f"group={group_id} added={names}")
        _group_notice(group_id, f"{actor['username']} added {names}")
        _push_group(group_id)
    emit('group_ok', {'message': 'Members added.'}, to=request.sid)


@socketio.on('group_remove_member')
@session_required
def handle_group_remove_member(data):
    data = data or {}
    group_id = data.get('group_id')
    target_id = data.get('user_id')
    actor = _group_admin(group_id)
    if not actor:
        return
    if not chat_room.can_manage_in_group(group_id, actor['id'], target_id):
        # Covers removing yourself, a peer admin and the owner, in one rule.
        emit('group_error',
             {'error': 'You cannot remove that member'}, to=request.sid)
        return
    target = chat_room.get_user(target_id)
    chat_room.remove_group_member(group_id, target_id)
    _audit(actor, 'group_remove_member', target_id, f"group={group_id}")
    name = (target or {}).get('username', 'someone')
    _group_notice(group_id, f"{actor['username']} removed {name}",
                  extra_user_ids=[target_id])
    _push_group(group_id)
    for sid in chat_room.get_sessions_for_user(target_id):
        emit('group_removed', {'group_id': group_id}, to=sid)
    emit('group_ok', {'message': 'Member removed.'}, to=request.sid)


@socketio.on('group_set_role')
@session_required
def handle_group_set_role(data):
    data = data or {}
    group_id = data.get('group_id')
    target_id = data.get('user_id')
    actor = _group_admin(group_id)
    if not actor:
        return
    if not chat_room.can_manage_in_group(group_id, actor['id'], target_id):
        emit('group_error', {'error': 'You cannot change that member'}, to=request.sid)
        return
    ok, error = chat_room.set_group_role(group_id, target_id, data.get('role'))
    if not ok:
        emit('group_error', {'error': error}, to=request.sid)
        return
    _audit(actor, 'group_set_role', target_id,
           f"group={group_id} role={data.get('role')}")
    name = (chat_room.get_user(target_id) or {}).get('username', 'someone')
    verb = 'made an admin' if data.get('role') == 'admin' else 'no longer an admin'
    _group_notice(group_id, f"{name} is {verb}")
    _push_group(group_id)
    emit('group_ok', {'message': 'Role updated.'}, to=request.sid)


@socketio.on('group_update_info')
@session_required
def handle_group_update_info(data):
    data = data or {}
    group_id = data.get('group_id')
    actor = _group_actor(group_id)
    if not actor:
        return
    if not chat_room.may_edit_group_info(group_id, actor['id']):
        emit('group_error',
             {'error': 'Only group admins can edit the group info'}, to=request.sid)
        return
    ok, error = chat_room.update_group_info(
        group_id, data.get('name'), data.get('description'))
    if not ok:
        emit('group_error', {'error': error}, to=request.sid)
        return
    _group_notice(group_id, f"{actor['username']} updated the group info")
    _push_group(group_id)
    emit('group_ok', {'message': 'Group updated.'}, to=request.sid)


@socketio.on('group_update_settings')
@session_required
def handle_group_update_settings(data):
    data = data or {}
    group_id = data.get('group_id')
    actor = _group_admin(group_id)
    if not actor:
        return
    ok, error = chat_room.update_group_settings(
        group_id,
        data.get('only_admins_post'),
        data.get('only_admins_edit_info'),
    )
    if not ok:
        emit('group_error', {'error': error}, to=request.sid)
        return
    _audit(actor, 'group_update_settings', None, f"group={group_id}")
    _group_notice(group_id, f"{actor['username']} changed the group settings")
    _push_group(group_id)
    emit('group_ok', {'message': 'Settings updated.'}, to=request.sid)


@socketio.on('group_leave')
@session_required
def handle_group_leave(data):
    data = data or {}
    group_id = data.get('group_id')
    actor = _group_actor(group_id)
    if not actor:
        return
    members_before = chat_room.get_group_member_ids(group_id)
    deleted, new_owner = chat_room.leave_group(group_id, actor['id'])
    if deleted:
        for sid in chat_room.get_sessions_for_user(actor['id']):
            emit('group_removed', {'group_id': group_id}, to=sid)
        emit('group_ok', {'message': 'You left; the group was empty and is gone.'},
             to=request.sid)
        return
    _group_notice(group_id, f"{actor['username']} left the group",
                  extra_user_ids=members_before)
    if new_owner:
        name = (chat_room.get_user(new_owner) or {}).get('username', 'someone')
        _group_notice(group_id, f"{name} is now the group owner")
    _push_group(group_id)
    for sid in chat_room.get_sessions_for_user(actor['id']):
        emit('group_removed', {'group_id': group_id}, to=sid)
    emit('group_ok', {'message': 'You left the group.'}, to=request.sid)
