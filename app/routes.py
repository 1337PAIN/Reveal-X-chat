"""
Route and Socket.IO handlers for Reveal-X.
"""

import base64
from datetime import datetime
import os
import secrets

import cv2
from flask import abort, render_template, request, send_file, redirect
from flask_socketio import emit
import numpy as np

from app import app, socketio
from app.models import chat_room
from app.vc_core import VisualCryptography


vc = VisualCryptography()
MAX_TEXT_LENGTH = 2000
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_VOICE_BYTES = 2 * 1024 * 1024
MAX_PROFILE_IMAGE_BYTES = 512 * 1024
ALLOWED_IMAGE_MIME_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/bmp'}
ALLOWED_REACTIONS = {'👍', '❤️', '😂', '😮', '😢', '🙏'}


def image_data_url(value):
    """Return a PNG data URL for either raw base64 or an existing data URL."""
    if not value:
        return ''
    if value.startswith('data:image'):
        return value
    return f'data:image/png;base64,{value}'


def parse_image_data_url(data_url):
    """Validate and decode an image data URL from the browser."""
    if not data_url or ',' not in data_url:
        raise ValueError('No valid image was received')

    header, encoded = data_url.split(',', 1)
    if not header.startswith('data:') or ';base64' not in header:
        raise ValueError('Only base64 image uploads are accepted')

    mime_type = header[5:].split(';', 1)[0].lower()
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValueError('Unsupported image type')

    image_bytes = base64.b64decode(encoded, validate=True)
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError('Image is too large')

    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError('Could not read image')

    return image


def client_message(message, current_user_id=None):
    """Convert persisted message fields into the shape expected by chat.js."""
    if not message:
        return None

    # Ensure message is a plain dict to avoid issues with Row objects
    msg_dict = dict(message)

    if msg_dict.get('type') == 'share':
        extra = msg_dict.get('extra', {})
        share1_token = extra.get('share1_token')
        share1_url = ''

        # Only show share1_url to the recipient
        if share1_token and current_user_id == msg_dict.get('recipient_id'):
            share1_url = f"/share1/{msg_dict['id']}?token={share1_token}"
        
        # Overwrite extra with a client-safe version
        msg_dict['extra'] = {
            'share1_url': share1_url,
            'share2_live': None,
            'timestamp': extra.get('timestamp'),
            'reply_to': extra.get('reply_to'),
            'forwarded_from': extra.get('forwarded_from'),
            'reactions': extra.get('reactions', {}),
        }
    
    return msg_dict


def emit_to_conversation(event_name, message):
    emit(event_name, client_message(message, message['sender_id']), to=request.sid)
    for session_id in chat_room.get_sessions_for_user(message['recipient_id']):
        emit(event_name, client_message(message, message['recipient_id']), to=session_id)


def reply_preview(message):
    if not message:
        return None
    content = message['content']
    if message['type'] == 'share':
        content = 'Encrypted image'
    elif message['type'] == 'voice':
        content = 'Voice message'
    return {
        'id': message['id'],
        'username': message['username'],
        'type': message['type'],
        'content': content[:120],
    }


def emit_read_receipts(reader_id, sender_id, message_ids, read_at):
    """Notify the sender that their messages were read."""
    if not message_ids:
        return

    payload = {
        'message_ids': message_ids,
        'reader_id': reader_id,
        'read_at': read_at,
    }
    for session_id in chat_room.get_sessions_for_user(sender_id):
        emit('messages_read', payload, to=session_id)


@app.route('/')
def index():
    """Serve main chat interface."""
    return render_template('index.html')


@app.route('/share1/<message_id>')
def get_share1(message_id):
    """Serve persisted Share 1 by unguessable token."""
    message = chat_room.get_share_message_by_token(message_id, request.args.get('token', ''))
    if not message:
        abort(404)

    filename = message['extra'].get('share1_filename')
    if not filename or os.path.basename(filename) != filename:
        abort(404)

    share_path = os.path.join(app.config['SHARES_FOLDER'], filename)
    if not os.path.isfile(share_path):
        abort(404)

    return send_file(share_path, mimetype='image/png', max_age=0)


@app.after_request
def add_security_headers(response):
    # [MODIFIED] HTTPS redirect and HSTS for production (check X-Forwarded-Proto for proxy)
    if request.headers.get('X-Forwarded-Proto', 'http') == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    elif request.url.startswith('http://'):
        return redirect(request.url.replace('http://', 'https://'), code=301)

    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.socket.io https://cdn.jsdelivr.net; "
        "style-src 'self' https://fonts.googleapis.com https://cdnjs.cloudflare.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: blob:; "
        "media-src 'self' data: blob:; "
        "connect-src 'self' ws: wss:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    return response


def emit_users():
    """Broadcast each connected user their account list with online state."""
    for session_id, user in list(chat_room.sessions.items()):
        emit('users_updated', {
            'users': chat_room.get_users_list(user['id'])
        }, to=session_id)


@socketio.on('connect')
def handle_connect():
    print(f"User connected: {request.sid}")


@socketio.on('disconnect')
def handle_disconnect():
    user = chat_room.remove_user(request.sid)
    if user:
        emit_users()
        print(f"User disconnected: {user['username']}")


@socketio.on('register')
def handle_register(data):
    """Create a persistent account and log the user in."""
    try:
        data = data or {}
        user, error = chat_room.create_user(data.get('username'), data.get('password'))
        if error:
            emit('auth_error', {'error': error}, to=request.sid)
            return

        chat_room.add_user(request.sid, user)
        chat_room.cleanup_old_messages()
        emit('auth_success', {
            'user': user,
            'users': chat_room.get_users_list(user['id'])
        }, to=request.sid)
        emit_users()
    except Exception as exc:
        print(f"Register failed: {exc}")
        emit('auth_error', {'error': 'Registration failed on the server'}, to=request.sid)


@socketio.on('login')
def handle_login(data):
    """Log in with a persisted account."""
    try:
        data = data or {}
        user, error = chat_room.authenticate(data.get('username'), data.get('password'))
        if error:
            emit('auth_error', {'error': error}, to=request.sid)
            return

        chat_room.add_user(request.sid, user)
        chat_room.cleanup_old_messages()
        emit('auth_success', {
            'user': user,
            'users': chat_room.get_users_list(user['id'])
        }, to=request.sid)
        emit_users()
    except Exception as exc:
        print(f"Login failed: {exc}")
        emit('auth_error', {'error': 'Login failed on the server'}, to=request.sid)


@socketio.on('logout')
def handle_logout():
    """Log out the current socket session."""
    user = chat_room.remove_user(request.sid)
    emit('logout_success', {}, to=request.sid)
    if user:
        emit_users()


@socketio.on('update_profile')
def handle_update_profile(data):
    """Update the current user's profile picture."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    profile_image = data.get('profile_image', '')
    if not current_user:
        return
    if profile_image:
        if not profile_image.startswith('data:image/') or ',' not in profile_image:
            emit('auth_error', {'error': 'Invalid profile image'}, to=request.sid)
            return
        header, encoded = profile_image.split(',', 1)
        if ';base64' not in header or header[5:].split(';', 1)[0].lower() not in ALLOWED_IMAGE_MIME_TYPES:
            emit('auth_error', {'error': 'Invalid profile image'}, to=request.sid)
            return
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception:
            emit('auth_error', {'error': 'Invalid profile image'}, to=request.sid)
            return
        if len(raw) > MAX_PROFILE_IMAGE_BYTES:
            emit('auth_error', {'error': 'Profile image is too large'}, to=request.sid)
            return

    chat_room.update_profile_image(current_user['id'], profile_image)
    current_user['profile_image'] = profile_image
    emit('profile_updated', {'profile_image': profile_image}, to=request.sid)
    emit_users()


@socketio.on('select_chat')
def handle_select_chat(data):
    """Send private chat history for the selected account."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    recipient_id = data.get('recipient_id')
    recipient = chat_room.get_user(recipient_id)

    if not current_user or not recipient:
        emit('chat_history', {'messages': [], 'recipient_id': recipient_id})
        return

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
def handle_message(data):
    """Handle private text messages."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    recipient_id = data.get('recipient_id')
    message = data.get('message', '').strip()
    reply_to_id = data.get('reply_to')

    if len(message) > MAX_TEXT_LENGTH:
        emit('message_error', {'error': 'Message is too long'})
        return

    if not current_user or not message or not chat_room.get_user(recipient_id):
        return

    extra = {}
    if reply_to_id:
        extra['reply_to'] = reply_preview(chat_room.get_message_for_user(reply_to_id, current_user['id']))

    msg_obj = chat_room.add_message(current_user['id'], recipient_id, 'text', message, extra)
    if not msg_obj:
        return

    emit('new_message', client_message(msg_obj, current_user['id']), to=request.sid)
    for session_id in chat_room.get_sessions_for_user(recipient_id):
        emit('new_message', client_message(msg_obj, recipient_id), to=session_id)


@socketio.on('send_voice')
def handle_voice(data):
    """Handle private voice messages."""
    # [MODIFIED] Added comment about virus scanning for voice uploads
    # In production, implement virus scanning for uploaded voice files
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    recipient_id = data.get('recipient_id')
    audio_data = data.get('audio', '')
    reply_to_id = data.get('reply_to')

    if not current_user or not chat_room.get_user(recipient_id):
        return
    if not audio_data.startswith('data:audio/') or ',' not in audio_data or ';base64' not in audio_data.split(',', 1)[0]:
        emit('message_error', {'error': 'Invalid voice message'})
        return

    try:
        audio_bytes = base64.b64decode(audio_data.split(',', 1)[1], validate=True)
    except Exception:
        emit('message_error', {'error': 'Invalid voice data'})
        return
    if not audio_bytes or len(audio_bytes) > MAX_VOICE_BYTES:
        emit('message_error', {'error': 'Voice message is too large'})
        return

    extra = {}
    if reply_to_id:
        extra['reply_to'] = reply_preview(chat_room.get_message_for_user(reply_to_id, current_user['id']))

    msg_obj = chat_room.add_message(current_user['id'], recipient_id, 'voice', audio_data, extra)
    if not msg_obj:
        return

    emit('new_message', client_message(msg_obj, current_user['id']), to=request.sid)
    for session_id in chat_room.get_sessions_for_user(recipient_id):
        emit('new_message', client_message(msg_obj, recipient_id), to=session_id)


@socketio.on('react_message')
def handle_reaction(data):
    """Set or replace the current user's reaction on a message."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    message_id = data.get('message_id')
    emoji = data.get('emoji') or ''

    if not current_user or emoji not in ALLOWED_REACTIONS:
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
    for session_id in chat_room.get_sessions_for_user(other_id):
        emit('message_reactions', payload, to=session_id)


@socketio.on('forward_message')
def handle_forward(data):
    """Forward an existing visible message to another account."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    message_id = data.get('message_id')
    recipient_id = data.get('recipient_id')

    if not current_user or not chat_room.get_user(recipient_id):
        return

    source = chat_room.get_message_for_user(message_id, current_user['id'])
    if not source or source['type'] == 'share':
        emit('message_error', {'error': 'Encrypted image shares cannot be forwarded'})
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

    emit('new_message', client_message(msg_obj, current_user['id']), to=request.sid)
    for session_id in chat_room.get_sessions_for_user(recipient_id):
        emit('new_message', client_message(msg_obj, recipient_id), to=session_id)


@socketio.on('mark_read')
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
def handle_typing(data):
    """Forward typing state to the selected recipient."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    recipient_id = data.get('recipient_id')
    is_typing = bool(data.get('is_typing'))

    if not current_user or not chat_room.get_user(recipient_id):
        return

    payload = {
        'sender_id': current_user['id'],
        'username': current_user['username'],
        'is_typing': is_typing,
    }
    for session_id in chat_room.get_sessions_for_user(recipient_id):
        emit('typing', payload, to=session_id)


@socketio.on('send_image')
def handle_image(data):
    """Handle private encrypted image sharing."""
    try:
        data = data or {}
        current_user = chat_room.get_session_user(request.sid)
        recipient_id = data.get('recipient_id')
        image_data = data.get('image', '')
        reply_to_id = data.get('reply_to')

        if not current_user:
            emit('image_error', {'error': 'Log in first'})
            return
        if not chat_room.get_user(recipient_id):
            emit('image_error', {'error': 'Select a user first'})
            return
        
        recipient_sessions = chat_room.get_sessions_for_user(recipient_id)
        if not recipient_sessions:
            emit('image_error', {'error': 'Recipient must be online to receive live Share 2'})
            return

        original_image = parse_image_data_url(image_data)
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        share1_token = secrets.token_urlsafe(32)
        share1_filename = f'share1_{timestamp}_{secrets.token_urlsafe(12)}.png'
        share1_path = os.path.join(app.config['SHARES_FOLDER'], share1_filename)

        share1, share2, original = vc.generate_shares_from_image(original_image)
        if not cv2.imwrite(share1_path, share1):
            emit('image_error', {'error': 'Could not save Share 1'})
            return

        ok2, share2_buffer = cv2.imencode('.png', share2)
        if not ok2:
            try:
                os.remove(share1_path)
            except OSError:
                pass
            emit('image_error', {'error': 'Could not encode live Share 2'})
            return

        share2_b64 = base64.b64encode(share2_buffer).decode('utf-8')

        extra = {
            'share1_filename': share1_filename,
            'share1_token': share1_token,
            'timestamp': timestamp
        }
        if reply_to_id:
            extra['reply_to'] = reply_preview(chat_room.get_message_for_user(reply_to_id, current_user['id']))

        msg_obj_db = chat_room.add_message(current_user['id'], recipient_id, 'share', 'Shared an encrypted image', extra)
        if not msg_obj_db:
            try:
                os.remove(share1_path)
            except OSError:
                pass
            emit('image_error', {'error': 'Image message could not be saved'})
            return

        # Sender should NOT see share1_url - only the recipient should have access to it
        sender_msg = client_message(msg_obj_db, current_user['id'])
        emit('new_message', sender_msg, to=request.sid)

        recipient_msg = client_message(msg_obj_db, recipient_id)
        recipient_msg_copy = recipient_msg.copy()
        recipient_msg_copy['extra'] = recipient_msg['extra'].copy()
        recipient_msg_copy['extra']['share2_live'] = f'data:image/png;base64,{share2_b64}'
        for session_id in recipient_sessions:
            emit('new_message', recipient_msg_copy, to=session_id)
    except ValueError as exc:
        emit('image_error', {'error': str(exc)})
    except Exception:
        emit('image_error', {'error': 'Image could not be sent'})


@socketio.on('revoke_share')
def handle_revoke_share(data):
    """[NEW] Revoke access to a share (one-time access revocation)."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    message_id = data.get('message_id')

    if not current_user or not message_id:
        return

    # Verify the user is either sender or recipient of the message
    message = chat_room.get_message_for_user(message_id, current_user['id'])
    if not message:
        return

    # Only allow revoking share messages
    if message.get('type') != 'share':
        return

    # Revoke access (mark as accessed)
    chat_room.revoke_share_access(message_id)

    # Notify the user that access has been revoked
    emit('share_revoked', {'message_id': message_id}, to=request.sid)


@socketio.on('delete_message')
def handle_delete_message(data):
    """Delete a message from the current user's view only."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    message_id = data.get('message_id')
    
    if not current_user or not message_id:
        return

    message = chat_room.get_message_for_user(message_id, current_user['id'])
    if not message:
        return

    emit('message_deleted', {'message_id': message_id}, to=request.sid)


@socketio.on('delete_account')
def handle_delete_account(data):
    """Delete user account and all their messages."""
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        emit('auth_error', {'error': 'Not logged in'})
        return
    
    user_id = current_user['id']
    
    # Delete all messages from/to this user
    chat_room.delete_user_messages(user_id)
    
    # Delete the user account
    chat_room.delete_user(user_id)
    
    # Remove from sessions
    chat_room.remove_user(request.sid)
    
    # Notify client to log out
    emit('logout_success', {}, to=request.sid)
    emit_users()
