"""The signed-in user's own account: public key, profile, username,
bio, status, two-factor enrolment, and deleting the account.
"""

from app.handlers.common import *  # noqa: F401,F403


@socketio.on('register_public_key')
@session_required
def handle_register_public_key(data):
    """Publish the caller's ECDH public key so peers can derive a shared secret.

    Only public key material reaches the server; the private half stays
    non-extractable in the browser.
    """
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    public_key = (data.get('public_key') or '').strip()
    if not current_user:
        return
    if not public_key or len(public_key) > MAX_PUBLIC_KEY_LENGTH:
        emit('auth_error', {'error': 'Invalid public key'}, to=request.sid)
        return
    try:
        base64.b64decode(public_key, validate=True)
    except Exception:
        emit('auth_error', {'error': 'Invalid public key'}, to=request.sid)
        return

    chat_room.set_public_key(current_user['id'], public_key)
    current_user['public_key'] = public_key
    emit('public_key_registered', {'public_key': public_key}, to=request.sid)
    emit_users()


@socketio.on('update_profile')
@session_required
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


@socketio.on('totp_begin_enrol')
@session_required
def handle_totp_begin_enrol(data):
    """Issue a new TOTP secret and its QR code.

    The secret is held in memory only. It is not written to the account until
    the user proves with a live code that their authenticator has it, so a
    half-finished setup can never lock anybody out.
    """
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        return
    if chat_room.get_totp_secret(current_user['id']):
        emit('settings_error', {'field': 'totp', 'error': 'Two-factor is already enabled'}, to=request.sid)
        return

    secret = generate_totp_secret()
    PENDING_TOTP_ENROLMENT[request.sid] = {'secret': secret, 'started': time.time()}

    label = current_user.get('email') or current_user['username']
    emit('totp_enrolment', {
        'secret': secret,
        'qr_data_url': totp_qr_data_url(secret, label),
    }, to=request.sid)


@socketio.on('totp_confirm_enrol')
@session_required
def handle_totp_confirm_enrol(data):
    """Turn two-factor on, once a code from the new secret checks out."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        return

    pending = PENDING_TOTP_ENROLMENT.get(request.sid)
    if not pending or time.time() - pending['started'] > TOTP_PENDING_TTL:
        PENDING_TOTP_ENROLMENT.pop(request.sid, None)
        emit('settings_error', {'field': 'totp', 'error': 'Setup timed out. Start again.'}, to=request.sid)
        return

    attempt = _reserve_reauthentication(current_user, 'totp')
    if attempt is None:
        return
    if not verify_totp_code(pending['secret'], data.get('code'), current_user['id']):
        emit('settings_error', {'field': 'totp', 'error': 'That code did not match. Check your authenticator.'}, to=request.sid)
        return
    _release_login_attempt(*attempt)

    chat_room.set_totp_secret(current_user['id'], pending['secret'])
    PENDING_TOTP_ENROLMENT.pop(request.sid, None)
    emit('totp_state', {'enabled': True}, to=request.sid)
    emit('settings_saved', {'totp_enabled': True}, to=request.sid)


@socketio.on('totp_disable')
@session_required
def handle_totp_disable(data):
    """Turn two-factor off, but only for someone holding a current code."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        return

    secret = chat_room.get_totp_secret(current_user['id'])
    if not secret:
        emit('totp_state', {'enabled': False}, to=request.sid)
        return

    attempt = _reserve_reauthentication(current_user, 'totp')
    if attempt is None:
        return
    if not verify_totp_code(secret, data.get('code'), current_user['id']):
        emit('settings_error', {'field': 'totp', 'error': 'Enter a current code to turn two-factor off.'}, to=request.sid)
        return
    _release_login_attempt(*attempt)

    chat_room.set_totp_secret(current_user['id'], '')
    emit('totp_state', {'enabled': False}, to=request.sid)
    emit('settings_saved', {'totp_enabled': False}, to=request.sid)


@socketio.on('update_username')
@session_required
def handle_update_username(data):
    """Rename the signed-in account."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        return

    user, error = chat_room.update_username(current_user['id'], data.get('new_username'))
    if error:
        emit('settings_error', {'field': 'username', 'error': error}, to=request.sid)
        return

    current_user['username'] = user['username']
    emit_to_conversation('settings_saved', {'username': user['username']}, current_user['id'])
    emit_users()


@socketio.on('update_bio')
@session_required
def handle_update_bio(data):
    """Store the short profile bio shown to other accounts."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        return

    bio = chat_room.update_bio(current_user['id'], data.get('bio'))
    current_user['bio'] = bio
    emit_to_conversation('settings_saved', {'bio': bio}, current_user['id'])
    emit_users()


@socketio.on('update_status')
@session_required
def handle_update_status(data):
    """Set availability (online / away / do not disturb)."""
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        return

    status = chat_room.update_status(current_user['id'], data.get('status'))
    current_user['status'] = status
    emit_to_conversation('settings_saved', {'status': status}, current_user['id'])
    emit_users()


def _confirm_identity(user, data):
    """Prove the person asking is the account holder, not a borrowed session.

    `totp_disable` already works this way -- it demands a current code so that
    someone with a borrowed session cannot quietly remove the second factor.
    Deleting the account is strictly more destructive than disabling 2FA and
    asked for nothing at all, which made an unlocked tab enough to destroy
    someone's account and every message in it.

    What counts as proof depends on how the account signs in, because not
    every account has a password: Firebase rows are created with an empty
    hash. So the password is required where one exists, a current code where
    two-factor is on, and an account that has neither -- a Google sign-in
    without 2FA -- has to type its own username instead. That last one is
    weaker than a secret, and it is meant to be: it stops a careless click,
    not an attacker. For those accounts the real defence is Google's.
    """
    user_id = user['id']

    secret = chat_room.get_totp_secret(user_id)
    if secret and not verify_totp_code(secret, data.get('totp'), user_id):
        return False, 'Enter a current six-digit code to delete this account.'

    if chat_room.has_password(user_id):
        account, error = chat_room.authenticate(user['username'], data.get('password'))
        if error or not account or account['id'] != user_id:
            return False, 'That password is not correct.'
        return True, None

    if (data.get('confirm_username') or '').strip() != user['username']:
        return False, f"Type the username {user['username']} exactly to confirm."
    return True, None



@socketio.on('delete_account')
@session_required
def handle_delete_account(data):
    """Delete the signed-in account, its messages and its stored shares.

    Irreversible, so it re-authenticates first and refuses to leave the
    server unadministered. Mirrors admin_delete_account from here on: the
    same purge, the same audit entry, and the same kicking of every other
    session the account holds.
    """
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        emit('auth_error', {'error': 'Not logged in'})
        return

    user_id = current_user['id']

    attempt = _reserve_reauthentication(current_user, 'delete_account')
    if attempt is None:
        return
    ok, error = _confirm_identity(current_user, data)
    if not ok:
        emit('settings_error', {'field': 'delete_account', 'error': error}, to=request.sid)
        return
    _release_login_attempt(*attempt)

    # count_admins(exclude_user_id=...) has existed since the admin panel was
    # built and was never called anywhere. The rank rules stop an admin being
    # removed by someone else, but nothing stopped the last one removing
    # themselves -- which leaves no one to approve registrations or reinstate
    # anybody, and no way back in short of restarting the server with
    # REVEALX_ADMIN_USER set.
    if (chat_room.is_admin(user_id)
            and chat_room.count_admins(exclude_user_id=user_id) == 0):
        emit('settings_error', {
            'field': 'delete_account',
            'error': ('You are the only administrator left. Promote someone '
                      'else before deleting this account.'),
        }, to=request.sid)
        return

    _audit(current_user, 'delete_account_self', current_user)
    # Every session, not just this tab: the account is about to stop existing
    # and any other device holding it would otherwise keep chatting as a user
    # the database no longer has.
    _disconnect_user_sessions(user_id, 'This account was deleted.')
    chat_room.purge_user(user_id)

    emit('logout_success', {}, to=request.sid)
    emit_users()
