"""Conversations: history, text and voice, reactions, forwarding,
read receipts, typing, revoke and delete.
"""

from app.handlers.common import *  # noqa: F401,F403


@socketio.on('select_chat')
@session_required
def handle_select_chat(data):
    """Send private chat history for the selected account."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    recipient_id = data.get('recipient_id')
    recipient = chat_room.get_user(recipient_id)

    if not current_user or not recipient or not chat_room.are_contacts(current_user['id'], recipient_id):
        emit('chat_history', {'messages': [], 'recipient_id': recipient_id})
        return
    # A locked chat's history waits for the chat-lock PIN in this session.
    if request.sid not in CHAT_LOCK_OPEN and chat_room.is_chat_locked(current_user['id'], recipient_id):
        emit('chat_history', {'messages': [], 'recipient_id': recipient_id, 'locked': True})
        return

    # Opening a chat normally marks it read, but a user who turned read receipts
    # off must not have that leak out just by looking at the conversation.
    if data.get('send_read_receipts', True):
        read_ids, read_at = chat_room.mark_messages_read(current_user['id'], recipient_id)
        emit_read_receipts(current_user['id'], recipient_id, read_ids, read_at)

    emit('chat_history', {
        'messages': [
            client_message(message, current_user['id'])
            for message in chat_room.get_conversation(current_user['id'], recipient_id)
        ],
        'recipient_id': recipient_id
    })


@socketio.on('send_message')
@session_required
def handle_message(data):
    """Handle private text messages, encrypted or plaintext."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    recipient_id = data.get('recipient_id')
    message = data.get('message', '').strip()
    reply_to_id = data.get('reply_to')
    encrypted = bool(data.get('encrypted'))
    iv = data.get('iv') or ''

    limit = MAX_CIPHERTEXT_LENGTH if encrypted else MAX_TEXT_LENGTH
    if len(message) > limit:
        emit('message_error', {'error': 'Message is too long'})
        return

    if not current_user or not message:
        return
    if not chat_room.can_message(current_user['id'], recipient_id):
        emit('message_error', {'error': NOT_A_CONTACT})
        return
    reply, reply_error = reply_for(reply_to_id, current_user['id'], peer_id=recipient_id)
    if reply_error:
        emit('message_error', {'error': reply_error})
        return
    if not _take_send_token(current_user['id'], 'message'):
        emit('message_error', {'error': SEND_TOO_FAST})
        return

    extra = {}
    if encrypted:
        if not iv or len(iv) > 64:
            emit('message_error', {'error': 'Invalid encrypted payload'})
            return
        envelope, error = _e2ee_envelope(data, current_user['id'], recipient_id)
        if error:
            emit('message_error', {'error': error})
            return
        extra['encrypted'] = True
        extra['iv'] = iv
        extra.update(envelope)
    if reply:
        extra['reply_to'] = reply

    msg_obj = chat_room.add_message(current_user['id'], recipient_id, 'text', message, extra)
    if not msg_obj:
        if encrypted:
            emit('message_error', {'error': DUPLICATE_MESSAGE_ERROR})
        return

    deliver_message(msg_obj, current_user['id'], recipient_id)


@socketio.on('send_voice')
@session_required
def handle_voice(data):
    """Handle private voice messages, encrypted or plaintext.

    Encrypted recordings arrive as raw base64 AES-GCM ciphertext plus the IV and
    the original container type; the server cannot decode them and simply stores
    them. Plaintext recordings still arrive as an audio data URL.
    """
    # In production, implement virus scanning for uploaded voice files.
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    recipient_id = data.get('recipient_id')
    audio_data = data.get('audio', '') or ''
    reply_to_id = data.get('reply_to')
    encrypted = bool(data.get('encrypted'))
    iv = data.get('iv') or ''
    mime = (data.get('mime') or '').lower().split(';', 1)[0].strip()

    if not current_user:
        return
    if not chat_room.can_message(current_user['id'], recipient_id):
        emit('message_error', {'error': NOT_A_CONTACT})
        return
    reply, reply_error = reply_for(reply_to_id, current_user['id'], peer_id=recipient_id)
    if reply_error:
        emit('message_error', {'error': reply_error})
        return
    if not _take_send_token(current_user['id'], 'voice'):
        emit('message_error', {'error': SEND_TOO_FAST})
        return

    extra = {}

    if encrypted:
        if not iv or len(iv) > 64:
            emit('message_error', {'error': 'Invalid encrypted payload'})
            return
        if mime not in ALLOWED_AUDIO_MIME_TYPES:
            emit('message_error', {'error': 'Unsupported audio type'})
            return
        try:
            audio_bytes = base64.b64decode(audio_data, validate=True)
        except Exception:
            emit('message_error', {'error': 'Invalid voice data'})
            return
        if not audio_bytes or len(audio_bytes) > MAX_VOICE_BYTES:
            emit('message_error', {'error': 'Voice message is too large'})
            return

        envelope, error = _e2ee_envelope(data, current_user['id'], recipient_id)
        if error:
            emit('message_error', {'error': error})
            return
        extra['encrypted'] = True
        extra['iv'] = iv
        extra['mime'] = mime
        extra.update(envelope)
    else:
        if not audio_data.startswith('data:audio/') or ',' not in audio_data or ';base64' not in audio_data.split(',', 1)[0]:
            emit('message_error', {'error': 'Invalid voice message'})
            return
        header, encoded = audio_data.split(',', 1)
        if header[5:].split(';', 1)[0].lower() not in ALLOWED_AUDIO_MIME_TYPES:
            emit('message_error', {'error': 'Unsupported audio type'})
            return
        try:
            audio_bytes = base64.b64decode(encoded, validate=True)
        except Exception:
            emit('message_error', {'error': 'Invalid voice data'})
            return
        if not audio_bytes or len(audio_bytes) > MAX_VOICE_BYTES:
            emit('message_error', {'error': 'Voice message is too large'})
            return

    if reply:
        extra['reply_to'] = reply

    msg_obj = chat_room.add_message(current_user['id'], recipient_id, 'voice', audio_data, extra)
    if not msg_obj:
        if encrypted:
            emit('message_error', {'error': DUPLICATE_MESSAGE_ERROR})
        return

    deliver_message(msg_obj, current_user['id'], recipient_id)


@socketio.on('react_message')
@session_required
def handle_reaction(data):
    """Set or replace the current user's reaction on a message."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    message_id = data.get('message_id')
    emoji = data.get('emoji') or ''

    # An empty emoji means "clear my reaction". set_reaction has always
    # supported that, but this guard rejected it before it could be reached,
    # so a reaction could be added and never taken back.
    if not current_user:
        return
    if emoji and emoji not in ALLOWED_REACTIONS:
        return

    message = chat_room.set_reaction(message_id, current_user['id'], emoji)
    if not message:
        return

    payload = {
        'message_id': message['id'],
        'reactions': message['extra'].get('reactions', {}),
    }
    emit('message_reactions', payload, to=request.sid)
    other_id = message['recipient_id'] if current_user['id'] == message['sender_id'] else message['sender_id']
    emit_to_conversation('message_reactions', payload, other_id)


@socketio.on('forward_message')
@session_required
def handle_forward(data):
    """Forward an existing visible message to another account."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    message_id = data.get('message_id')
    recipient_id = data.get('recipient_id')

    if not current_user:
        return
    if not chat_room.can_message(current_user['id'], recipient_id):
        emit('message_error', {'error': NOT_A_CONTACT})
        return

    source = chat_room.get_message_for_user(message_id, current_user['id'])
    if not source or source['type'] == 'share':
        emit('message_error', {'error': 'Image shares cannot be forwarded'})
        return
    if source.get('encrypted'):
        # Ciphertext is bound to one conversation's derived key, so forwarding
        # it verbatim would hand the new recipient something undecryptable.
        emit('message_error', {'error': 'Encrypted messages cannot be forwarded'})
        return

    extra = {
        'forwarded_from': {
            'username': source['username'],
            'type': source['type'],
        }
    }
    msg_obj = chat_room.add_message(current_user['id'], recipient_id, source['type'], source['content'], extra)
    if not msg_obj:
        return

    deliver_message(msg_obj, current_user['id'], recipient_id)


@socketio.on('mark_read')
@session_required
def handle_mark_read(data):
    """Mark messages from the selected sender as read."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    sender_id = data.get('sender_id')

    if not current_user or not chat_room.get_user(sender_id):
        return

    read_ids, read_at = chat_room.mark_messages_read(current_user['id'], sender_id)
    emit_read_receipts(current_user['id'], sender_id, read_ids, read_at)


@socketio.on('typing')
@session_required
def handle_typing(data):
    """Forward typing state to the selected recipient."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    recipient_id = data.get('recipient_id')
    is_typing = bool(data.get('is_typing'))

    if not current_user or not chat_room.can_message(current_user['id'], recipient_id):
        return

    payload = {
        'sender_id': current_user['id'],
        'username': current_user['username'],
        'is_typing': is_typing,
    }
    emit_to_conversation('typing', payload, recipient_id)


@socketio.on('send_image')
@session_required
def handle_retired_send_image(_data=None):
    """The server-side split, retired.

    send_image uploaded the image itself and the server split it, so the
    server held every picture in memory and Share 2 could only be handed over
    live. send_share replaced it (the browser splits; see vc-client.js). This
    stub stays so that a page still running the old script hears why its
    image did not send, instead of nothing at all. It reads nothing from the
    payload: the point is that no image is decoded here any more.
    """
    emit('image_error', {'error': 'Images are now split in your browser. Reload the page to update.'})


@socketio.on('revoke_share')
@session_required
def handle_revoke_share(data):
    """Revoke access to a share (burns its one-time access)."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    message_id = data.get('message_id')

    if not current_user or not message_id:
        return

    message = chat_room.get_message_for_user(message_id, current_user['id'])
    if not message or message['type'] != 'share':
        return

    chat_room.revoke_share_access(message_id)

    payload = {'message_id': message_id}
    emit('share_revoked', payload, to=request.sid)
    emit_to_conversation(
        'share_revoked', payload,
        message['sender_id'], message['recipient_id'],
        exclude=request.sid,
    )


@socketio.on('delete_message')
@session_required
def handle_delete_message(data):
    """Delete a message.

    The sender deletes for everyone, so both sides are told. The recipient
    deletes only their own copy, so the event goes to their sessions alone.
    """
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    message_id = data.get('message_id')

    if not current_user or not message_id:
        return

    result = chat_room.delete_message(message_id, current_user['id'])
    if not result:
        return

    scope, notify_ids = result
    payload = {'message_id': message_id, 'scope': scope}
    emit('message_deleted', payload, to=request.sid)
    emit_to_conversation('message_deleted', payload, *notify_ids, exclude=request.sid)
