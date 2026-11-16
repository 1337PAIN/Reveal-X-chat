"""Shared by every handler module: imports, limits, throttles, the shapes
messages are sent in, session plumbing and the helpers several modules use.

routes.py was one 2,400-line file holding all of this and every handler.
It is split by concern under app/handlers/; this is the part they share.
"""

import base64
from datetime import datetime
import functools
import re
import hmac
import os
import secrets
import threading
import time

import binascii

import cv2
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key
from flask import abort, jsonify, render_template, request, send_file, redirect, url_for
from flask_socketio import emit
import numpy as np

from app import app, socketio
from app.models import FORBIDDEN_EXTRA_KEYS, chat_room
from app.timeutil import utcnow
from app.log import audit as audit_log, log
from app import state, sweeper
from app.vc_core import VisualCryptography, VCReconstructor
from app.ai_security import (
    TamperDetector,
    enhance_image,
    hmac_image,
    image_to_data_url,
    data_url_to_image,
    prediction_to_dict,
    psnr,
    resize_max,
    sha256_image,
    ssim,
    mse,
)
from app.ai_security.attacks import tamper_share
from app.ai_security.tamper_detector import FEATURE_NAMES
from app.auth import (
    firebase_client_config,
    firebase_enabled,
    generate_totp_secret,
    totp_qr_data_url,
    verify_totp_code,
)
from app.auth.firebase_auth import FirebaseAuthError, claims_to_identity, verify_id_token

# Initialize the components used for secure image sharing and reconstruction.
vc = VisualCryptography()
reconstructor = VCReconstructor()
tamper_detector = TamperDetector()
MAX_TEXT_LENGTH = 2000
# The largest base64 AES-GCM ciphertext a MAX_TEXT_LENGTH message can produce:
# up to 4 UTF-8 bytes a character, plus the 16-byte tag, base64'd. This was
# 4000, which fits 2,000 ASCII characters but refused a long message in Urdu
# or with emoji -- invisible while the field's maxlength was 500, and a
# refusal of text the field accepted once it was raised to the real limit.
MAX_CIPHERTEXT_LENGTH = 4 * -(-(MAX_TEXT_LENGTH * 4 + 16) // 3)
MAX_IMAGE_BYTES = 6 * 1024 * 1024

#: Client-side split (send_share). Share 1 is random, so its PNG barely
#: compresses: about one byte per pixel. The client downscales to 1200 px on
#: the long side, so 1600 leaves headroom without inviting huge uploads.
MAX_SHARE_SIDE = 1600
MIN_SHARE_SIDE = 8
MAX_SHARE1_BYTES = 3 * 1024 * 1024
#: Share 2 as a PNG, base64'd into JSON, encrypted, base64'd again: ~1.8x its
#: pixel count. Kept, with Share 1, under the 8 MB Socket.IO buffer.
MAX_SHARE_ENVELOPE_LENGTH = 4_500_000
#: Pearson chi-square bound on Share 1's histogram. A genuine share is uniform
#: (df = 255, mean 255, sd ~22.6); this is ten standard deviations out, so a
#: real share is essentially never refused, while any ordinary picture is
#: off the scale. It catches a client that uploads the image by mistake. It
#: is not a defence against a sender who wants to leak their own picture --
#: nothing on the server could be.
SHARE_CHI_SQUARE_LIMIT = 255 + 10 * (2 * 255) ** 0.5
MAX_VOICE_BYTES = 2 * 1024 * 1024
MAX_PROFILE_IMAGE_BYTES = 512 * 1024
MAX_PUBLIC_KEY_LENGTH = 512
ALLOWED_IMAGE_MIME_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/bmp'}
ALLOWED_AUDIO_MIME_TYPES = {'audio/webm', 'audio/ogg', 'audio/mp4', 'audio/mpeg', 'audio/wav'}
ALLOWED_REACTIONS = {'👍', '❤️', '😂', '😮', '😢', '🙏'}

# Sign-ins waiting on a TOTP code: {socket_id: {user_id, started, attempts}}.
# Held in memory only, so a dropped connection abandons the attempt.
PENDING_TOTP = {}
TOTP_PENDING_TTL = 5 * 60
MAX_TOTP_ATTEMPTS = 5

# TOTP secrets that have been shown to a user but not yet proven with a code.
PENDING_TOTP_ENROLMENT = {}

# Failed password attempts: {key: [timestamps]}. The client shows an attempts
# counter, but that lives in the browser and an attacker scripting the socket
# never sees it -- so the limit has to be enforced here as well.
#
# Two buckets, because either alone is easy to sidestep. Per-account stops
# someone grinding one password list against one victim; per-address stops
# spraying one common password across many accounts. Both are needed.
#
# Held in app.state (shared between workers when REVEALX_REDIS_URL is set);
# every check-and-count there is one atomic step. FAILED_LOGINS is the
# in-memory table itself, kept addressable for the tests.
FAILED_LOGINS = state.store().window('login') if state.store().kind == 'memory' else {}
LOGIN_MAX_ATTEMPTS = 8           # per account, within the window
# Per address, across all accounts. Deliberately much looser than the per
# account limit: everyone behind one router, or one lab machine, shares an
# address, so a tight value here punishes bystanders for someone else's typos.
# It exists to catch spraying -- fifty failures from one address in a quarter
# of an hour is clearly not a person mistyping -- and the per-account limit is
# what actually protects an individual account.
#
# Note this reads request.remote_addr unless REVEALX_TRUST_PROXY is set.
# Behind a reverse proxy that is the proxy, so every user would share one
# bucket; trusting forwarded headers is a deployment decision and is
# deliberately not assumed here. See _client_address.
LOGIN_IP_MAX_ATTEMPTS = 50
LOGIN_WINDOW_SECONDS = 15 * 60


def _client_address():
    """The address to throttle against.

    request.remote_addr is the proxy when one is in front, which would put
    every user in a single bucket. Trusting forwarded headers fixes that but
    only behind a proxy you control, so it is opt-in: REVEALX_TRUST_PROXY.

    With trust on, the address comes from, in order:

    1. REVEALX_CLIENT_IP_HEADER, if set and present -- a single-value header
       the edge *overwrites*, such as True-Client-IP from Cloudflare.
    2. X-Forwarded-For, counting REVEALX_PROXY_HOPS entries (default 1) in
       from the right. Each trusted proxy appends the address it received the
       connection from, so the Nth entry from the right is the one written by
       the outermost of N trusted proxies.

    This used to take the *left-most* entry, on the reasoning that it is "the
    original client". It is whatever the client chose to send: proxies append
    to an incoming X-Forwarded-For rather than replacing it, so a client
    sending `X-Forwarded-For: 1.2.3.4` arrives as `1.2.3.4, <real address>`.
    Rotating that value gave every request a fresh bucket, which made the
    login, registration and lab limits unlimited for anyone setting a header.
    """
    if os.environ.get('REVEALX_TRUST_PROXY', '').lower() != 'true':
        return request.remote_addr or 'unknown'

    named = os.environ.get('REVEALX_CLIENT_IP_HEADER', '').strip()
    if named:
        value = request.headers.get(named, '').strip()
        if value:
            return value

    try:
        hops = max(int(os.environ.get('REVEALX_PROXY_HOPS', '1')), 1)
    except ValueError:
        hops = 1
    entries = [e.strip() for e in request.headers.get('X-Forwarded-For', '').split(',')
               if e.strip()]
    # Fewer entries than trusted hops means the request did not pass through
    # the proxies we expect, so nothing in the header can be believed.
    if len(entries) >= hops:
        return entries[-hops]
    return request.remote_addr or 'unknown'


#: Per-user token buckets for the send paths: (burst, tokens per second).
#:
#: Sign-in, registration and the lab were limited and sending was not, so one
#: signed-in socket could loop send_message and fill the database and every
#: recipient's screen. send_image is worse -- each one decodes, splits, writes
#: a file and runs the detector, on a server with one worker. A bucket allows
#: a burst for ordinary use and refills steadily, so a person never notices it
#: and a loop runs dry within seconds. In-process, like the other limits.
SEND_LIMITS = {
    'message': (30, 1.0),       # 30 at once, then one a second
    'voice': (10, 1 / 6),       # 10 at once, then one every 6 s
    'image': (5, 1 / 15),       # 5 at once, then one every 15 s
    'contact': (10, 1 / 30),    # contact requests: 10 at once, then one every 30 s
}

#: Said when a send, a forward or an image share goes to someone who is not a
#: contact (handlers/contacts.py). The same words for an unknown account, so
#: the refusal does not tell anyone which ids exist.
NOT_A_CONTACT = 'You can only message your contacts. Add them by username first.'
#: Said for a username nobody has, and for one that cannot be added.
NO_SUCH_ACCOUNT = 'No account with that username.'
# The in-memory buckets themselves, kept addressable for the tests.
SEND_BUCKETS = state.store().buckets if state.store().kind == 'memory' else {}
SEND_TOO_FAST = 'You are sending too fast. Wait a moment and try again.'
_send_clock = time.monotonic


def _take_send_token(user_id, kind):
    """Spend one token from this user's bucket. False when it is empty."""
    capacity, rate = SEND_LIMITS[kind]
    return state.store().take_token((user_id, kind), capacity, rate, _send_clock())


def _login_keys(username, address):
    return ('account', (username or '').strip().lower()), ('address', address or 'unknown')


#: The two laboratory endpoints are deliberately open: the demonstration has to
#: work without an account. That is a reasonable trade for a coursework demo and
#: an unreasonable one for an endpoint that decodes an 8 MB image and runs share
#: generation, an attack simulation and inference on every call. Measured, one
#: /api/lab/process is ~0.35 s of CPU and /api/lab/features ~0.73 s, so a single
#: client can occupy a core indefinitely for free.
#:
#: This is a ceiling on abuse, not an authentication control. It is per address
#: and in-process, so it does not survive a restart and does not coordinate
#: across workers. The project runs one worker (Socket.IO keeps sessions in
#: process), so in this deployment that is the whole of it.
LAB_RATE = state.store().window('lab') if state.store().kind == 'memory' else {}
LAB_MAX_REQUESTS = 20            # per address, within the window
LAB_WINDOW_SECONDS = 60


def _lab_rate_limited(address):
    """Seconds to wait before this address may call a lab endpoint, or 0."""
    return state.store().window_reserve('lab', [(address, LAB_MAX_REQUESTS)],
                                        LAB_WINDOW_SECONDS, time.time())


#: Registration is unauthenticated and the first thing it does is hash a
#: password with Argon2id -- 64 MiB and ~100 ms by design, which is correct for
#: storing a password and dangerous as something any anonymous client can
#: trigger in a loop. The project runs one worker, so a few requests a second
#: is enough to occupy it completely. This caps the rate; it is not an
#: authentication control.
REGISTER_RATE = state.store().window('register') if state.store().kind == 'memory' else {}
REGISTER_MAX_REQUESTS = 5        # per address, within the window
REGISTER_WINDOW_SECONDS = 600


def _register_rate_limited(address):
    """Seconds to wait before this address may request another account, or 0."""
    return state.store().window_reserve('register', [(address, REGISTER_MAX_REQUESTS)],
                                        REGISTER_WINDOW_SECONDS, time.time())


def _login_limits(username, address):
    account_key, address_key = _login_keys(username, address)
    return [(account_key, LOGIN_MAX_ATTEMPTS), (address_key, LOGIN_IP_MAX_ATTEMPTS)]


def _login_blocked(username, address):
    """Is either bucket over its limit? Returns seconds to wait, or 0."""
    return state.store().window_wait('login', _login_limits(username, address),
                                     LOGIN_WINDOW_SECONDS, time.time())


def _reserve_login_attempt(username, address):
    """Check the buckets and count this attempt, atomically, before verifying.

    Returns (wait, reservation). A non-zero wait means refuse; otherwise the
    attempt has already been counted as a failure, and the caller gives the
    reservation back with _release_login_attempt if it turns out not to be one.

    This used to be check, verify, then record. Argon2 takes ~100 ms by design
    and the server has 100 threads, so every request arriving inside that
    window read the same under-limit bucket and went on to verify: forty
    parallel guesses all reached the password check against a limit of eight.
    Reserving first means guesses in flight count, not only finished ones.
    """
    now = time.time()
    wait = state.store().window_reserve('login', _login_limits(username, address),
                                        LOGIN_WINDOW_SECONDS, now)
    return (wait, None) if wait else (0, now)


def _release_login_attempt(username, address, reservation):
    """Hand back a reservation for an attempt that was not a wrong guess."""
    if reservation is None:
        return
    state.store().window_remove('login', list(_login_keys(username, address)), reservation)


def _reserve_reauthentication(current_user, field):
    """Reserve an attempt in the sign-in buckets for a signed-in re-check.

    totp_disable, totp_confirm_enrol and delete_account each ask for a secret
    again, and none of them was limited. They are the doors a borrowed
    session uses, and the second factor exists precisely for that case: six
    digits fall to ~333,000 guesses, which a scripted socket gets through in
    minutes when nothing counts them. They now draw on the same account and
    address buckets as sign-in, so guesses split between the doors still add
    up to one limit.

    Returns a context to pass to _release_login_attempt on success, or None
    after telling the client to wait.
    """
    username, address = current_user.get('username'), _client_address()
    wait, reservation = _reserve_login_attempt(username, address)
    if wait:
        emit('settings_error', {
            'field': field,
            'error': f'Too many failed attempts. Try again in {max(wait // 60, 1)} minute(s).',
        }, to=request.sid)
        return None
    return username, address, reservation


def _clear_failed_logins(username, address):
    state.store().window_clear('login', list(_login_keys(username, address)))


def client_message(message, current_user_id=None):
    """Convert persisted message fields into the shape expected by chat.js."""
    if not message:
        return None

    msg_dict = dict(message)

    if msg_dict.get('type') == 'share':
        extra = msg_dict.get('extra', {})
        share1_token = extra.get('share1_token')
        share1_url = ''
        share1_analysis_url = ''

        # Only the recipient is handed the capability token. The HMAC tag and
        # the stored filename never leave the server.
        if share1_token and current_user_id == msg_dict.get('recipient_id'):
            share1_url = f"/share1/{msg_dict['id']}?token={share1_token}"
            share1_analysis_url = f"/api/share/{msg_dict['id']}/analysis?token={share1_token}"

        msg_dict['extra'] = {
            'share1_url': share1_url,
            'share1_analysis_url': share1_analysis_url,
            'share2_live': None,
            'timestamp': extra.get('timestamp'),
            'reply_to': extra.get('reply_to'),
            'forwarded_from': extra.get('forwarded_from'),
            'reactions': extra.get('reactions', {}),
            'encrypted': extra.get('encrypted', False),
            'iv': extra.get('iv'),
            'v': extra.get('v'),
            'msg_uuid': extra.get('msg_uuid'),
            'sent_at': extra.get('sent_at'),
            'epk': extra.get('epk'),
        }
    else:
        extra = msg_dict.get('extra', {})
        msg_dict['extra'] = {
            'reply_to': extra.get('reply_to'),
            'forwarded_from': extra.get('forwarded_from'),
            'reactions': extra.get('reactions', {}),
            'encrypted': extra.get('encrypted', False),
            'iv': extra.get('iv'),
            'mime': extra.get('mime'),
            # The recipient rebuilds the AES-GCM associated data from these,
            # so they must come back exactly as the sender wrote them.
            'v': extra.get('v'),
            'msg_uuid': extra.get('msg_uuid'),
            'sent_at': extra.get('sent_at'),
            'epk': extra.get('epk'),
        }

    return msg_dict


def emit_to_conversation(event_name, payload, *user_ids, exclude=None):
    """Send the same payload to every live session of the listed users."""
    seen = set()
    for user_id in user_ids:
        for session_id in chat_room.get_sessions_for_user(user_id):
            if session_id == exclude or session_id in seen:
                continue
            seen.add(session_id)
            emit(event_name, payload, to=session_id)


def deliver_message(msg_obj, sender_id, recipient_id):
    """Emit a new message to every live session on both sides.

    The payload is rebuilt per side because only the recipient may receive the
    Share 1 capability token.
    """
    sender_sessions = set(chat_room.get_sessions_for_user(sender_id)) | {request.sid}
    sender_payload = client_message(msg_obj, sender_id)
    for session_id in sender_sessions:
        emit('new_message', sender_payload, to=session_id)

    recipient_payload = client_message(msg_obj, recipient_id)
    for session_id in chat_room.get_sessions_for_user(recipient_id):
        emit('new_message', recipient_payload, to=session_id)


def reply_preview(message):
    if not message:
        return None
    content = message['content']
    if message['type'] == 'share':
        content = 'Image share'
    elif message['type'] == 'voice':
        content = 'Voice message'
    elif message.get('encrypted'):
        # The server cannot read E2EE text, so it must not try to preview it.
        content = 'Encrypted message'
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
    emit_to_conversation('messages_read', payload, sender_id)


def emit_users():
    """Broadcast each connected user their account list with online state."""
    for session_id, user in chat_room.all_sessions():
        emit('users_updated', {
            'users': chat_room.get_users_list(user['id'])
        }, to=session_id)


#: Events a socket may send before it has a session. Every other handler is
#: wrapped in session_required, so it answers `auth_required` instead of
#: returning silently. tests/test_resume.py enumerates the server's handler
#: table against this set, so a new handler is covered without being listed.
PRE_AUTH_EVENTS = frozenset({
    'register', 'login', 'firebase_login', 'verify_totp', 'resume_session', 'logout',
})

AUTH_REQUIRED_MESSAGE = 'Your session has ended. Sign in again.'


def session_required(handler):
    """Tell a socket with no session that it has none.

    Handlers used to check for a session and return silently without one.
    After a dropped connection the page came back on a new socket with no
    session, still looked signed in, and every action vanished: no message
    sent, nothing said why. The client now hears `auth_required`, tries its
    resume token, and asks the user to sign in only if that fails.
    """
    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        if not chat_room.get_session_user(request.sid):
            emit('auth_required', {'error': AUTH_REQUIRED_MESSAGE}, to=request.sid)
            return None
        return handler(*args, **kwargs)
    return wrapper


def _establish_session(user, resume_token=None):
    """Attach a fully authenticated user to this socket.

    The failed-login buckets are cleared *here* rather than when the password
    verified. Clearing them earlier meant a correct password wiped the record
    before the second factor had been shown, which is half of why the TOTP
    attempt limit could be reset at will.
    """
    _clear_failed_logins(user.get('username'), _client_address())
    # Refresh from the database so role and account_state are current even if
    # the caller passed an older copy.
    fresh = chat_room.get_auth_user(user['id']) or user
    chat_room.add_user(request.sid, fresh)
    chat_room.cleanup_old_messages()
    # A resumed session keeps the token it presented rather than being given
    # a fresh one: re-issuing would let a token renew itself for ever, and
    # the 12 hours are meant to bound how long a sign-in lasts.
    token = resume_token or chat_room.issue_resume_token(fresh['id'])
    emit('auth_success', {
        'user': fresh,
        'users': chat_room.get_users_list(fresh['id']),
        'resume_token': token,
        'resumed': bool(resume_token),
    }, to=request.sid)
    # Pending contact requests, both ways (handlers/contacts.py).
    emit('contacts_updated', chat_room.contact_requests(fresh['id']), to=request.sid)
    # Admins get the account list immediately, so the panel and its pending
    # badge are populated before they open it.
    if chat_room.is_admin(fresh['id']):
        accounts = chat_room.list_accounts()
        emit('admin_accounts', {
            'accounts': accounts,
            'pending': sum(1 for a in accounts if a['account_state'] == 'pending'),
        }, to=request.sid)
    emit_users()


def _complete_sign_in(user):
    """Finish sign-in, or hold it pending a second factor.

    The first factor (Firebase or password) is never enough on its own once
    TOTP is enabled: the socket gets no session until a valid code arrives, so
    an attacker holding only the password cannot read or send anything.
    """
    if chat_room.get_totp_secret(user['id']):
        PENDING_TOTP[request.sid] = {
            'user_id': user['id'],
            # Carried so a wrong code can be recorded against the account and
            # the address, not only against this socket.
            'username': user['username'],
            'address': _client_address(),
            'started': time.time(),
            'attempts': 0,
        }
        emit('totp_required', {'username': user['username']}, to=request.sid)
        return
    _establish_session(user)


# ---------------------------------------------------------------------------
# Administration
#
# Every handler below funnels through _require_admin(). The client is told
# whether it is an admin only so it can decide what to draw -- that flag is a
# rendering hint and is never trusted here. Authority comes from the server's
# own session record and a fresh database read of the caller's role, so
# promoting yourself in devtools achieves nothing.
# ---------------------------------------------------------------------------

def _require_admin():
    """Return the calling admin's user record, or None (and emit the error)."""
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        emit('admin_error', {'error': 'Not signed in'}, to=request.sid)
        return None
    # Re-read the role rather than trusting the copy cached in the session:
    # an admin demoted mid-session should stop being one immediately.
    if not chat_room.is_admin(current_user['id']):
        emit('admin_error', {'error': 'Administrator access required'}, to=request.sid)
        return None
    return current_user


def _audit(actor, action, target=None, detail=''):
    """Record an administrative action.

    Destructive admin actions previously left no trace at all: an account
    could be deleted, or its password reset, with nothing to show who did it
    or when. Writes to the log rather than the database on purpose -- an audit
    trail an admin can edit through the same panel is not much of one.

    Deliberately records usernames and never passwords. Written as one JSON
    object per line through app.log.audit; it was "[audit] key=value" text,
    where a value with a space or a newline could read as a second entry.
    """
    target_name = ''
    if target:
        record = chat_room.get_user(target) if isinstance(target, str) else target
        target_name = (record or {}).get('username', target if isinstance(target, str) else '')
    audit_log(actor=actor['username'], action=action, target=target_name, detail=detail)


def _require_superadmin():
    current_user = _require_admin()
    if not current_user:
        return None
    if not chat_room.is_superadmin(current_user['id']):
        emit('admin_error', {'error': 'Only the superadmin can do that'}, to=request.sid)
        return None
    return current_user


def _require_target(actor, target_id):
    """Check that `actor` outranks `target`, or emit the reason why not.

    Rank must be strictly greater, so an admin can act on users but not on
    another admin and never on the superadmin. Acting on yourself is refused
    here too: 'disable my own account' and 'demote myself' would both be ways
    to strand the deployment without an administrator.
    """
    if not target_id:
        emit('admin_error', {'error': 'No account given'}, to=request.sid)
        return False
    if target_id == actor['id']:
        emit('admin_error', {'error': 'You cannot do that to your own account'}, to=request.sid)
        return False
    if not chat_room.get_user(target_id):
        emit('admin_error', {'error': 'No such account'}, to=request.sid)
        return False
    if not chat_room.can_manage(actor['id'], target_id):
        target_role = chat_room.get_role(target_id)
        emit('admin_error', {
            'error': ('The superadmin account cannot be modified'
                      if target_role == 'superadmin'
                      else 'Only the superadmin can manage other administrators'),
        }, to=request.sid)
        return False
    return True


def _admin_session_ids():
    ids = []
    for sid, user in chat_room.all_sessions():
        if chat_room.is_admin(user['id']):
            ids.append(sid)
    return ids


def _push_accounts_to_admins():
    """Keep every signed-in admin's panel current."""
    accounts = chat_room.list_accounts()
    pending = sum(1 for a in accounts if a['account_state'] == 'pending')
    for sid in _admin_session_ids():
        emit('admin_accounts', {'accounts': accounts, 'pending': pending}, to=sid)


def _notify_admins_of_pending():
    _push_accounts_to_admins()


def _disconnect_user_sessions(user_id, reason):
    """Kick every live session belonging to a user.

    Without this, disabling or deleting an account leaves any socket it already
    holds fully functional until the tab is closed -- the account would be
    'disabled' but still chatting.
    """
    for sid in list(chat_room.get_sessions_for_user(user_id) or []):
        emit('force_signed_out', {'reason': reason}, to=sid)
        chat_room.remove_user(sid)
    emit_users()


#: E2EE protocol version, the only one accepted and the only one the client
#: opens. Rows of older versions still on the server expire within the hour.
E2EE_PROTOCOL = 4
#: Base64 SPKI of a P-256 public key is 124 characters; anything longer is
#: not one, and is refused before it is decoded.
E2EE_MAX_EPK_LENGTH = 200
#: How far a message's sent_at (the sender's clock) may be from the server's.
#: The client's check of the *authenticated* sent_at is the security boundary;
#: this only refuses obviously wrong values at the door.
E2EE_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000
_MSG_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
DUPLICATE_MESSAGE_ERROR = 'This message was already delivered'


def _ephemeral_key_problem(epk, sender_id):
    """Why `epk` is not an acceptable v4 ephemeral key, or None.

    It must be base64 SPKI of a point on P-256, which is what the browser can
    import. It must also not be the sender's own long-term key: sending that
    would quietly turn v4 back into a static-static scheme with no forward
    secrecy, and an honest client never does.
    """
    if not isinstance(epk, str) or not epk or len(epk) > E2EE_MAX_EPK_LENGTH:
        return 'Invalid encrypted payload'
    try:
        key = load_der_public_key(base64.b64decode(epk, validate=True))
    except (binascii.Error, ValueError, TypeError):
        return 'Invalid encrypted payload'
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        return 'Invalid encrypted payload'
    sender = chat_room.get_user(sender_id) or {}
    if sender.get('public_key') and sender['public_key'].strip() == epk:
        return 'Invalid encrypted payload'
    return None


def _e2ee_envelope(data, sender_id, recipient_id):
    """Validate the v4 envelope of an encrypted send.

    Returns (fields_to_store, error). The server cannot check the ciphertext,
    but it can make sure the values the recipient authenticates it with --
    `v`, `msg_uuid`, `sent_at` and `epk` -- are present, well formed, and
    (for msg_uuid) unused in this conversation. Older versions are refused:
    v1 used one key for both directions, v2 bound neither the conversation nor
    the time, and v3 derived every message key from the two long-term keys,
    so a key taken later opened every stored message. v4's per-message key
    `epk` is what gives forward secrecy for the sender.

    sent_at must be an integer within E2EE_MAX_CLOCK_SKEW_MS of this clock.
    These are sanity checks on an honest sender, not a defence: the server is
    the party E2EE does not trust, and the recipient's browser re-checks the
    authenticated values itself.
    """
    version = data.get('v')
    if version != E2EE_PROTOCOL or isinstance(version, bool):
        return None, 'Unsupported encryption version. Reload the page.'
    msg_uuid = data.get('msg_uuid')
    if not isinstance(msg_uuid, str) or not _MSG_UUID_RE.match(msg_uuid):
        return None, 'Invalid encrypted payload'
    sent_at = data.get('sent_at')
    if not isinstance(sent_at, int) or isinstance(sent_at, bool):
        return None, 'Invalid encrypted payload'
    if abs(sent_at - int(time.time() * 1000)) > E2EE_MAX_CLOCK_SKEW_MS:
        return None, "This device's clock is more than 5 minutes off. Correct it and resend."
    epk = data.get('epk')
    problem = _ephemeral_key_problem(epk, sender_id)
    if problem:
        return None, problem
    if chat_room.has_message_uuid(sender_id, recipient_id, msg_uuid):
        return None, DUPLICATE_MESSAGE_ERROR
    return {'v': E2EE_PROTOCOL, 'msg_uuid': msg_uuid, 'sent_at': sent_at, 'epk': epk}, None


def _share_hmac(image):
    return hmac_image(image, app.config['SHARE_HMAC_KEY'])

# Everything above, private names included, is what the handler modules
# import with "from app.handlers.common import *".
__all__ = [name for name in list(globals()) if not name.startswith('__')]
