"""Signing in and out: resume, connect/disconnect, register, password
and Firebase sign-in, the second factor, logout.
"""

from app.handlers.common import *  # noqa: F401,F403


@socketio.on('resume_session')
def handle_resume_session(data):
    """Restore a session on a new socket from a resume token.

    Within the token's 12 hours this skips the password and the second
    factor: the token can only have been issued after both were satisfied
    (_establish_session is the only place that issues one), and it dies
    early on logout or any change to the account.
    """
    data = data or {}
    token = data.get('token')
    user_id = chat_room.redeem_resume_token(token if isinstance(token, str) else None)
    user = chat_room.get_auth_user(user_id) if user_id else None
    if not user:
        emit('auth_required', {'error': 'Your session expired. Sign in again.',
                               'resume_failed': True}, to=request.sid)
        return
    _establish_session(user, resume_token=token)


@socketio.on('connect')
def handle_connect():
    sweeper.ensure_started(chat_room)
    log.debug('Socket connected: %s', request.sid)


@socketio.on('disconnect')
def handle_disconnect():
    # Abandon any half-finished second factor or TOTP setup with the socket.
    PENDING_TOTP.pop(request.sid, None)
    PENDING_TOTP_ENROLMENT.pop(request.sid, None)

    user = chat_room.remove_user(request.sid)
    if user:
        emit_users()
        log.debug('User disconnected: %s', user['username'])


@socketio.on('register')
def handle_register(data):
    """Submit an account *request*. It does not sign anyone in.

    Self-service registration creates a 'pending' row that cannot authenticate
    until an administrator approves it. The reply is deliberately the same
    whether or not the username was already taken -- see below.
    """
    try:
        data = data or {}

        # Before create_user, because create_user is the expensive part.
        wait = _register_rate_limited(_client_address())
        if wait:
            emit('auth_error', {
                'error': f'Too many account requests. Try again in {max(wait // 60, 1)} minute(s).',
            }, to=request.sid)
            return

        user, error = chat_room.create_user(
            data.get('username'), data.get('password'), account_state='pending'
        )
        if error:
            # A duplicate username is the one error we do not report honestly.
            # Since requests are not visible to the requester, an accurate
            # "already exists" would turn this form into a way to enumerate who
            # holds an account. Validation errors about the caller's own input
            # are safe to return.
            if error == 'Username already exists':
                emit('registration_submitted', {
                    'message': 'Request submitted. An administrator will review it.',
                }, to=request.sid)
                return
            emit('auth_error', {'error': error}, to=request.sid)
            return

        _notify_admins_of_pending()
        emit('registration_submitted', {
            'message': 'Request submitted. An administrator will review it.',
        }, to=request.sid)
    except Exception as exc:
        log.exception('Register failed: %s', exc)
        emit('auth_error', {'error': 'Registration failed on the server'}, to=request.sid)


@socketio.on('login')
def handle_login(data):
    """Log in with a persisted account."""
    try:
        data = data or {}
        username = data.get('username')

        address = _client_address()

        wait, reservation = _reserve_login_attempt(username, address)
        if wait:
            # Deliberately the same message whether or not the account exists,
            # so this does not become a way to test which usernames are real.
            emit('auth_error', {
                'error': f'Too many failed attempts. Try again in {max(wait // 60, 1)} minute(s).',
            }, to=request.sid)
            return

        user, error = chat_room.authenticate(username, data.get('password'))
        # Only a wrong password counts toward the throttle, so the reservation
        # is handed back for anything else. "Awaiting approval" and "disabled"
        # are returned *after* the password has already verified, so they are
        # not guesses -- counting them meant someone who kept trying while
        # waiting for approval was locked out for 15 minutes the moment an
        # admin approved them. An exception keeps the reservation: failing
        # closed is the safe direction for a lockout.
        if error != chat_room.ERROR_BAD_CREDENTIALS:
            _release_login_attempt(username, address, reservation)
        if error:
            emit('auth_error', {'error': error}, to=request.sid)
            return

        # Not cleared here: the sign-in is not finished if a second factor is
        # still outstanding. _establish_session clears it once it really is.
        _complete_sign_in(user)
    except Exception as exc:
        log.exception('Login failed: %s', exc)
        emit('auth_error', {'error': 'Login failed on the server'}, to=request.sid)


@socketio.on('firebase_login')
def handle_firebase_login(data):
    """Sign in with a Firebase ID token (Google or Firebase email/password).

    The token is a JWT signed by Google. Nothing the client says about its own
    identity is trusted: the UID is taken only from verified claims.
    """
    try:
        data = data or {}
        claims = verify_id_token(data.get('id_token'))
        identity = claims_to_identity(claims)

        user, error = chat_room.get_or_create_firebase_user(
            identity['firebase_uid'],
            email=identity['email'],
            display_name=identity['display_name'],
            provider=identity['provider'],
        )
        if error:
            emit('auth_error', {'error': error}, to=request.sid)
            return
        _complete_sign_in(user)
    except FirebaseAuthError as exc:
        emit('auth_error', {'error': str(exc)}, to=request.sid)
    except Exception as exc:
        log.exception('Firebase login failed: %s', exc)
        emit('auth_error', {'error': 'Sign-in failed on the server'}, to=request.sid)


@socketio.on('verify_totp')
def handle_verify_totp(data):
    """Second factor for a sign-in that is waiting on a code."""
    data = data or {}
    pending = PENDING_TOTP.get(request.sid)

    if not pending or time.time() - pending['started'] > TOTP_PENDING_TTL:
        PENDING_TOTP.pop(request.sid, None)
        emit('auth_error', {'error': 'Sign-in timed out. Please start again.'}, to=request.sid)
        return

    # MAX_TOTP_ATTEMPTS alone counted against this socket, and PENDING_TOTP is
    # keyed on request.sid -- so disconnecting and signing in again bought five
    # fresh guesses, for ever. Six digits with a one-step tolerance is about
    # 333,000 expected guesses, which is a couple of hours at that rate: the
    # second factor was no obstacle to anyone who already had the password.
    #
    # A wrong code is now a failed authentication attempt like any other, so it
    # lands in the same account and address buckets a wrong password does, and
    # those outlive the socket.
    username, address = pending.get('username'), pending.get('address')

    # Reserved before the code is checked, for the same reason as a password:
    # see _reserve_login_attempt.
    wait, reservation = _reserve_login_attempt(username, address)
    if wait:
        PENDING_TOTP.pop(request.sid, None)
        emit('auth_error', {
            'error': f'Too many failed attempts. Try again in {max(wait // 60, 1)} minute(s).',
        }, to=request.sid)
        return

    pending['attempts'] += 1
    if pending['attempts'] > MAX_TOTP_ATTEMPTS:
        _release_login_attempt(username, address, reservation)
        PENDING_TOTP.pop(request.sid, None)
        emit('auth_error', {'error': 'Too many incorrect codes. Please sign in again.'}, to=request.sid)
        return

    secret = chat_room.get_totp_secret(pending['user_id'])
    if not verify_totp_code(secret, data.get('code'), pending['user_id']):
        # Already counted by the reservation.
        remaining = MAX_TOTP_ATTEMPTS - pending['attempts']
        emit('totp_error', {
            'error': 'Incorrect code',
            'attempts_remaining': max(remaining, 0),
        }, to=request.sid)
        return

    _release_login_attempt(username, address, reservation)
    PENDING_TOTP.pop(request.sid, None)
    user = chat_room.get_auth_user(pending['user_id'])
    if not user:
        emit('auth_error', {'error': 'Account is no longer available'}, to=request.sid)
        return
    _establish_session(user)


@socketio.on('logout')
def handle_logout():
    """Log out the current socket session.

    Also ends every resume token the account holds, on every device: signing
    out is the user saying this session is over, and a token that outlived it
    would bring it back on the next reconnect.
    """
    user = chat_room.remove_user(request.sid)
    if user:
        chat_room.bump_session_epoch(user['id'])
    emit('logout_success', {}, to=request.sid)
    if user:
        emit_users()
