"""
Route and Socket.IO handlers for Reveal-X.
"""

import base64
from datetime import datetime
import hmac
import os
import secrets
import time

import cv2
from flask import abort, jsonify, render_template, request, send_file, redirect
from flask_socketio import emit
import numpy as np

from app import app, socketio
from app.models import chat_room
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


vc = VisualCryptography()
reconstructor = VCReconstructor()
tamper_detector = TamperDetector()
MAX_TEXT_LENGTH = 2000
# Base64 AES-GCM ciphertext runs roughly 1.4x the plaintext plus the tag.
MAX_CIPHERTEXT_LENGTH = 4000
MAX_IMAGE_BYTES = 6 * 1024 * 1024
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
FAILED_LOGINS = {}
LOGIN_MAX_ATTEMPTS = 8           # per account, within the window
# Per address, across all accounts. Deliberately much looser than the per
# account limit: everyone behind one router, or one lab machine, shares an
# address, so a tight value here punishes bystanders for someone else's typos.
# It exists to catch spraying -- fifty failures from one address in a quarter
# of an hour is clearly not a person mistyping -- and the per-account limit is
# what actually protects an individual account.
#
# Note this reads request.remote_addr. Behind a reverse proxy that is the
# proxy, so every user would share one bucket; trusting X-Forwarded-For is a
# deployment decision and is deliberately not assumed here.
LOGIN_IP_MAX_ATTEMPTS = 50
LOGIN_WINDOW_SECONDS = 15 * 60


def _client_address():
    """The address to throttle against.

    request.remote_addr is the proxy when one is in front, which would put
    every user in a single bucket. X-Forwarded-For fixes that but is trivially
    spoofable by the client, so trusting it is opt-in: set REVEALX_TRUST_PROXY
    only when a proxy you control actually sets that header.
    """
    if os.environ.get('REVEALX_TRUST_PROXY', '').lower() == 'true':
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            # Left-most entry is the original client; the rest are proxies.
            return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _login_keys(username, address):
    return ('account', (username or '').strip().lower()), ('address', address or 'unknown')


def _login_blocked(username, address):
    """Is either bucket over its limit? Returns seconds to wait, or 0."""
    now = time.time()
    account_key, address_key = _login_keys(username, address)
    for key, limit in ((account_key, LOGIN_MAX_ATTEMPTS),
                       (address_key, LOGIN_IP_MAX_ATTEMPTS)):
        hits = [t for t in FAILED_LOGINS.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
        FAILED_LOGINS[key] = hits
        if len(hits) >= limit:
            return int(LOGIN_WINDOW_SECONDS - (now - hits[0])) + 1
    return 0


def _record_failed_login(username, address):
    now = time.time()
    for key in _login_keys(username, address):
        FAILED_LOGINS.setdefault(key, []).append(now)
    # The dict is only ever appended to, so drop buckets that have aged out
    # rather than growing it forever.
    if len(FAILED_LOGINS) > 2048:
        for key in list(FAILED_LOGINS):
            FAILED_LOGINS[key] = [t for t in FAILED_LOGINS[key]
                                  if now - t < LOGIN_WINDOW_SECONDS]
            if not FAILED_LOGINS[key]:
                del FAILED_LOGINS[key]


def _clear_failed_logins(username, address):
    for key in _login_keys(username, address):
        FAILED_LOGINS.pop(key, None)


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
        content = 'Encrypted image'
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


@app.route('/')
def index():
    """Serve main chat interface."""
    return render_template('index.html')


@app.route('/lab')
def ai_lab():
    """Serve the final AI/ML visual cryptography lab dashboard."""
    return render_template('lab.html')


@app.route('/api/auth/config')
def auth_config():
    """Tell the browser which sign-in methods this deployment offers.

    The Firebase web config is public by design - it identifies the project to
    Google. The service-account key that verifies tokens stays server-side and
    is never sent here.
    """
    config = firebase_client_config()
    return jsonify({
        # Only advertise Firebase when the server can actually verify its
        # tokens, otherwise the button would lead to a dead end.
        'firebase': config if (config and firebase_enabled()) else None,
        'password_login': True,
        'totp': True,
    })


@app.route('/sw.js')
def service_worker():
    """Serve the service worker from the root.

    A worker registered at /static/sw.js gets the scope /static/, so it can
    never control '/' or '/lab' -- the offline cache existed but applied to
    nothing the user actually visits. Serving it from the root gives it the
    whole-origin scope it was written for.
    """
    response = send_file(os.path.join(app.static_folder, 'sw.js'),
                         mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route('/lab/parity')
def lab_parity():
    """Harness proving the browser and the server compute identical features.

    The ONNX model is only meaningful in the browser if the 13 statistics fed
    to it are the same ones NumPy/OpenCV produced during training. This page
    generates shares, extracts features on both sides, and diffs them. It is
    evidence, not decoration -- if a refactor breaks parity, this goes red.
    """
    return render_template('lab_parity.html')


@app.route('/api/lab/features', methods=['POST'])
def lab_features():
    """Return the server's own feature vector and probability for an image.

    Used by /lab/parity as the reference implementation to diff against.
    """
    payload = request.get_json(silent=True) or {}
    try:
        image = data_url_to_image(payload.get('image', ''))
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400

    features = tamper_detector.extract_features(image)
    prediction = tamper_detector.predict(image)
    return jsonify({
        'ok': True,
        'shape': [int(image.shape[0]), int(image.shape[1])],
        'feature_names': FEATURE_NAMES,
        'features': [float(v) for v in features],
        'probability_tampered': prediction.probability_tampered,
        'model_name': prediction.model_name,
    })


def _share_hmac(image):
    return hmac_image(image, app.config['SECRET_KEY'])


@app.route('/api/lab/process', methods=['POST'])
def process_ai_lab_image():
    """Generate VC shares, simulate tampering, run ML detection, reconstruct, enhance and score."""
    started = time.perf_counter()
    try:
        payload = request.get_json(silent=True) or {}
        image_data = payload.get('image', '')
        attack = (payload.get('attack') or 'none').lower()
        strength = float(payload.get('strength') or 0.35)
        strength = max(0.05, min(strength, 0.75))

        original_image = resize_max(data_url_to_image(image_data), max_side=512)
        share1, share2, original = vc.generate_shares_from_image(original_image)
        clean_share1 = share1.copy()
        received_share1 = tamper_share(clean_share1, attack=attack, strength=strength)

        clean_hash = sha256_image(clean_share1)
        received_hash = sha256_image(received_share1)
        clean_hmac = _share_hmac(clean_share1)
        received_hmac = _share_hmac(received_share1)
        hmac_match = hmac.compare_digest(clean_hmac, received_hmac)

        prediction = tamper_detector.predict(received_share1)
        reconstructed = reconstructor.reconstruct_xor(received_share1, share2)
        enhanced_candidate = enhance_image(reconstructed)

        reconstructed_metrics = {
            'mse': round(mse(original, reconstructed), 4),
            'psnr': round(psnr(original, reconstructed), 4),
            'ssim': round(ssim(original, reconstructed), 4),
        }
        candidate_metrics = {
            'mse': round(mse(original, enhanced_candidate), 4),
            'psnr': round(psnr(original, enhanced_candidate), 4),
            'ssim': round(ssim(original, enhanced_candidate), 4),
        }
        # When enhancement would lower SSIM (common on a badly damaged
        # reconstruction) the plain reconstruction is reported instead. The flag
        # is surfaced so the dashboard can say so rather than quietly swapping.
        quality_safe_fallback = candidate_metrics['ssim'] < reconstructed_metrics['ssim']
        enhanced = reconstructed if quality_safe_fallback else enhanced_candidate
        enhanced_metrics = reconstructed_metrics.copy() if quality_safe_fallback else candidate_metrics

        response = {
            'ok': True,
            'attack': attack,
            'strength': strength,
            'integrity': {
                'exact_tamper_detected': not hmac_match,
                'sha256_original_share1': clean_hash,
                'sha256_received_share1': received_hash,
                'hmac_match': hmac_match,
            },
            'ml': prediction_to_dict(prediction),
            'metrics': {
                'reconstructed': reconstructed_metrics,
                'enhanced': enhanced_metrics,
                'enhanced_candidate': candidate_metrics,
                'processing_ms': round((time.perf_counter() - started) * 1000, 2),
                'quality_safe_fallback': quality_safe_fallback,
            },
            'images': {
                'original': image_to_data_url(original),
                'share1_clean': image_to_data_url(clean_share1),
                'share1_received': image_to_data_url(received_share1),
                'share2': image_to_data_url(share2),
                'reconstructed': image_to_data_url(reconstructed),
                'enhanced': image_to_data_url(enhanced),
                'heatmap': prediction.heatmap_data_url,
            },
        }
        return jsonify(response)
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except Exception as exc:
        print(f"AI lab processing failed: {exc}")
        return jsonify({'ok': False, 'error': 'Processing failed on the server'}), 500


def _load_stored_share1(message):
    """Read a stored Share 1 from disk, or None when it is gone."""
    filename = message['extra'].get('share1_filename')
    if not filename or os.path.basename(filename) != filename:
        return None, None
    share_path = os.path.join(app.config['SHARES_FOLDER'], filename)
    if not os.path.isfile(share_path):
        return None, None
    image = cv2.imread(share_path, cv2.IMREAD_GRAYSCALE)
    return image, share_path


@app.route('/api/share/<message_id>/analysis')
def analyse_share(message_id):
    """Verify and classify a stored Share 1 before the recipient reconstructs it.

    This is the chat-side counterpart of the /lab dashboard: the share has sat
    on the server and crossed the network since it was generated, so this is the
    point where tampering can actually be caught. Holding the capability token
    is the authorisation, and analysis never burns the one-time access.
    """
    started = time.perf_counter()
    message = chat_room.get_share_message_by_token(
        message_id, request.args.get('token', ''), consume=False
    )
    if not message:
        return jsonify({'ok': False, 'error': 'Share is unavailable, revoked or already opened'}), 404

    share1, _ = _load_stored_share1(message)
    if share1 is None:
        return jsonify({'ok': False, 'error': 'Stored Share 1 is missing'}), 404

    expected_hmac = message['extra'].get('share1_hmac', '')
    current_hmac = _share_hmac(share1)
    hmac_match = bool(expected_hmac) and hmac.compare_digest(current_hmac, expected_hmac)

    prediction = tamper_detector.predict(share1)

    return jsonify({
        'ok': True,
        'message_id': message_id,
        'integrity': {
            'hmac_match': hmac_match,
            'exact_tamper_detected': not hmac_match,
            'sha256_original_share1': message['extra'].get('share1_sha256', ''),
            'sha256_received_share1': sha256_image(share1),
        },
        'ml': prediction_to_dict(prediction),
        'processing_ms': round((time.perf_counter() - started) * 1000, 2),
    })


@app.route('/share1/<message_id>')
def get_share1(message_id):
    """Serve persisted Share 1 by unguessable token, consuming its one-time access."""
    message = chat_room.get_share_message_by_token(
        message_id, request.args.get('token', ''), consume=True
    )
    if not message:
        abort(404)

    _, share_path = _load_stored_share1(message)
    if not share_path:
        abort(404)

    return send_file(share_path, mimetype='image/png', max_age=0)


@app.after_request
def add_security_headers(response):
    # HSTS/HTTPS redirect are enabled only in production to keep localhost easy to run.
    force_https = os.environ.get('REVEAL_X_FORCE_HTTPS', '').lower() == 'true'
    if request.headers.get('X-Forwarded-Proto', 'http') == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    elif force_https and request.url.startswith('http://'):
        return redirect(request.url.replace('http://', 'https://'), code=301)

    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        # www.gstatic.com serves the Firebase SDK; apis.google.com and the
        # identitytoolkit/securetoken endpoints are what Firebase Auth talks to.
        # 'wasm-unsafe-eval' lets onnxruntime-web compile the tamper-detection
        # model. It permits WebAssembly compilation only -- unlike
        # 'unsafe-eval' it does not re-enable eval() or new Function().
        "script-src 'self' 'wasm-unsafe-eval' https://cdn.socket.io https://cdn.jsdelivr.net "
        "https://www.gstatic.com https://apis.google.com; "
        "style-src 'self' https://fonts.googleapis.com https://cdnjs.cloudflare.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: blob: https://*.googleusercontent.com; "
        "media-src 'self' data: blob:; "
        "connect-src 'self' blob: ws: wss: "
        "https://identitytoolkit.googleapis.com https://securetoken.googleapis.com "
        "https://www.googleapis.com; "
        # The Google sign-in popup renders in a frame owned by these origins.
        "frame-src 'self' https://accounts.google.com https://*.firebaseapp.com; "
        # ORT's wasm glue may spin up a same-origin blob worker.
        "worker-src 'self' blob:; "
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
    # Abandon any half-finished second factor or TOTP setup with the socket.
    PENDING_TOTP.pop(request.sid, None)
    PENDING_TOTP_ENROLMENT.pop(request.sid, None)

    user = chat_room.remove_user(request.sid)
    if user:
        emit_users()
        print(f"User disconnected: {user['username']}")


def _establish_session(user):
    """Attach a fully authenticated user to this socket."""
    # Refresh from the database so role and account_state are current even if
    # the caller passed an older copy.
    fresh = chat_room.get_auth_user(user['id']) or user
    chat_room.add_user(request.sid, fresh)
    chat_room.cleanup_old_messages()
    emit('auth_success', {
        'user': fresh,
        'users': chat_room.get_users_list(fresh['id']),
    }, to=request.sid)
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

    Deliberately records usernames and never passwords.
    """
    target_name = ''
    if target:
        record = chat_room.get_user(target) if isinstance(target, str) else target
        target_name = (record or {}).get('username', target if isinstance(target, str) else '')
    stamp = datetime.utcnow().isoformat(timespec='seconds')
    line = f"[audit] {stamp}Z actor={actor['username']} action={action}"
    if target_name:
        line += f" target={target_name}"
    if detail:
        line += f" {detail}"
    print(line, flush=True)


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
    for sid, user in list(chat_room.sessions.items()):
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


@socketio.on('admin_list_accounts')
def handle_admin_list_accounts():
    if not _require_admin():
        return
    accounts = chat_room.list_accounts()
    pending = sum(1 for a in accounts if a['account_state'] == 'pending')
    emit('admin_accounts', {'accounts': accounts, 'pending': pending}, to=request.sid)


@socketio.on('admin_set_state')
def handle_admin_set_state(data):
    """Approve a request, disable an account, or re-enable a disabled one."""
    admin = _require_admin()
    if not admin:
        return
    data = data or {}
    target_id = data.get('user_id')
    state = data.get('state')

    if not _require_target(admin, target_id):
        return

    ok, error = chat_room.set_account_state(target_id, state)
    if not ok:
        emit('admin_error', {'error': error}, to=request.sid)
        return

    _audit(admin, 'set_state', target_id, f'state={state}')
    if state != 'active':
        _disconnect_user_sessions(target_id, 'Your account was disabled by an administrator.')
    _push_accounts_to_admins()
    emit('admin_ok', {'message': f'Account set to {state}.'}, to=request.sid)
    emit_users()


@socketio.on('admin_create_account')
def handle_admin_create_account(data):
    """Create an account directly, already approved."""
    admin = _require_admin()
    if not admin:
        return
    data = data or {}
    make_admin = bool(data.get('make_admin'))
    if make_admin and not chat_room.is_superadmin(admin['id']):
        emit('admin_error', {'error': 'Only the superadmin can create administrators'},
             to=request.sid)
        return

    user, error = chat_room.create_user(
        data.get('username'), data.get('password'),
        account_state='active',
        role='admin' if make_admin else 'user',
    )
    if error:
        emit('admin_error', {'error': error}, to=request.sid)
        return
    _audit(admin, 'create_account', user['id'],
           f"role={'admin' if make_admin else 'user'}")
    _push_accounts_to_admins()
    emit('admin_ok', {'message': f"Created {user['username']}."}, to=request.sid)
    emit_users()


@socketio.on('admin_reset_password')
def handle_admin_reset_password(data):
    admin = _require_admin()
    if not admin:
        return
    data = data or {}
    if not _require_target(admin, data.get('user_id')):
        return
    ok, error = chat_room.set_password(data.get('user_id'), data.get('password'))
    if not ok:
        emit('admin_error', {'error': error}, to=request.sid)
        return
    _audit(admin, 'reset_password', data.get('user_id'))
    # Old sessions keep working after a reset, which would defeat the point of
    # resetting a compromised account's password.
    _disconnect_user_sessions(data.get('user_id'),
                              'Your password was changed by an administrator.')
    emit('admin_ok', {'message': 'Password updated.'}, to=request.sid)


@socketio.on('admin_delete_account')
def handle_admin_delete_account(data):
    admin = _require_admin()
    if not admin:
        return
    data = data or {}
    target_id = data.get('user_id')

    if not _require_target(admin, target_id):
        return
    target = chat_room.get_user(target_id)

    _audit(admin, 'delete_account', target)
    _disconnect_user_sessions(target_id, 'Your account was removed by an administrator.')
    chat_room.purge_user(target_id)
    _push_accounts_to_admins()
    emit('admin_ok', {'message': f"Deleted {target['username']} and their messages."},
         to=request.sid)
    emit_users()


@socketio.on('admin_set_role')
def handle_admin_set_role(data):
    """Promote a user to administrator, or demote one back.

    Superadmin only, and it cannot mint another superadmin: that rank comes
    from the environment at startup and nowhere else, so there is exactly one
    and it cannot be granted through the UI.
    """
    admin = _require_superadmin()
    if not admin:
        return
    data = data or {}
    target_id = data.get('user_id')
    if not _require_target(admin, target_id):
        return

    ok, error = chat_room.set_role(target_id, data.get('role'))
    if not ok:
        emit('admin_error', {'error': error}, to=request.sid)
        return

    _audit(admin, 'set_role', target_id, f"role={data.get('role')}")

    # Their session caches a role, and the roster they can see depends on it.
    _disconnect_user_sessions(target_id, 'Your account permissions were changed.')
    _push_accounts_to_admins()
    emit('admin_ok', {'message': 'Role updated.'}, to=request.sid)
    emit_users()


@socketio.on('register')
def handle_register(data):
    """Submit an account *request*. It does not sign anyone in.

    Self-service registration creates a 'pending' row that cannot authenticate
    until an administrator approves it. The reply is deliberately the same
    whether or not the username was already taken -- see below.
    """
    try:
        data = data or {}
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
        print(f"Register failed: {exc}")
        emit('auth_error', {'error': 'Registration failed on the server'}, to=request.sid)


@socketio.on('login')
def handle_login(data):
    """Log in with a persisted account."""
    try:
        data = data or {}
        username = data.get('username')

        address = _client_address()

        wait = _login_blocked(username, address)
        if wait:
            # Deliberately the same message whether or not the account exists,
            # so this does not become a way to test which usernames are real.
            emit('auth_error', {
                'error': f'Too many failed attempts. Try again in {max(wait // 60, 1)} minute(s).',
            }, to=request.sid)
            return

        user, error = chat_room.authenticate(username, data.get('password'))
        if error:
            # Only a wrong password counts toward the throttle. "Awaiting
            # approval" and "disabled" are returned *after* the password has
            # already verified, so they are not guesses -- counting them meant
            # someone who kept trying while waiting for approval was locked out
            # for 15 minutes the moment an admin approved them.
            if error == chat_room.ERROR_BAD_CREDENTIALS:
                _record_failed_login(username, address)
            emit('auth_error', {'error': error}, to=request.sid)
            return

        _clear_failed_logins(username, address)
        _complete_sign_in(user)
    except Exception as exc:
        print(f"Login failed: {exc}")
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
        print(f"Firebase login failed: {exc}")
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

    pending['attempts'] += 1
    if pending['attempts'] > MAX_TOTP_ATTEMPTS:
        PENDING_TOTP.pop(request.sid, None)
        emit('auth_error', {'error': 'Too many incorrect codes. Please sign in again.'}, to=request.sid)
        return

    secret = chat_room.get_totp_secret(pending['user_id'])
    if not verify_totp_code(secret, data.get('code'), pending['user_id']):
        remaining = MAX_TOTP_ATTEMPTS - pending['attempts']
        emit('totp_error', {
            'error': 'Incorrect code',
            'attempts_remaining': max(remaining, 0),
        }, to=request.sid)
        return

    PENDING_TOTP.pop(request.sid, None)
    user = chat_room.get_auth_user(pending['user_id'])
    if not user:
        emit('auth_error', {'error': 'Account is no longer available'}, to=request.sid)
        return
    _establish_session(user)


@socketio.on('logout')
def handle_logout():
    """Log out the current socket session."""
    user = chat_room.remove_user(request.sid)
    emit('logout_success', {}, to=request.sid)
    if user:
        emit_users()


@socketio.on('register_public_key')
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

    if not verify_totp_code(pending['secret'], data.get('code'), current_user['id']):
        emit('settings_error', {'field': 'totp', 'error': 'That code did not match. Check your authenticator.'}, to=request.sid)
        return

    chat_room.set_totp_secret(current_user['id'], pending['secret'])
    PENDING_TOTP_ENROLMENT.pop(request.sid, None)
    emit('totp_state', {'enabled': True}, to=request.sid)
    emit('settings_saved', {'totp_enabled': True}, to=request.sid)


@socketio.on('totp_disable')
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

    if not verify_totp_code(secret, data.get('code'), current_user['id']):
        emit('settings_error', {'field': 'totp', 'error': 'Enter a current code to turn two-factor off.'}, to=request.sid)
        return

    chat_room.set_totp_secret(current_user['id'], '')
    emit('totp_state', {'enabled': False}, to=request.sid)
    emit('settings_saved', {'totp_enabled': False}, to=request.sid)


@socketio.on('update_username')
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

    if not current_user or not message or not chat_room.get_user(recipient_id):
        return

    extra = {}
    if encrypted:
        if not iv or len(iv) > 64:
            emit('message_error', {'error': 'Invalid encrypted payload'})
            return
        extra['encrypted'] = True
        extra['iv'] = iv
    if reply_to_id:
        extra['reply_to'] = reply_preview(chat_room.get_message_for_user(reply_to_id, current_user['id']))

    msg_obj = chat_room.add_message(current_user['id'], recipient_id, 'text', message, extra)
    if not msg_obj:
        return

    deliver_message(msg_obj, current_user['id'], recipient_id)


@socketio.on('send_voice')
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

    if not current_user or not chat_room.get_user(recipient_id):
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

        extra['encrypted'] = True
        extra['iv'] = iv
        extra['mime'] = mime
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

    if reply_to_id:
        extra['reply_to'] = reply_preview(chat_room.get_message_for_user(reply_to_id, current_user['id']))

    msg_obj = chat_room.add_message(current_user['id'], recipient_id, 'voice', audio_data, extra)
    if not msg_obj:
        return

    deliver_message(msg_obj, current_user['id'], recipient_id)


@socketio.on('react_message')
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
    emit_to_conversation('typing', payload, recipient_id)


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

        # Re-read what actually landed on disk so the tag covers the bytes the
        # recipient will later download, not the in-memory array.
        stored_share1 = cv2.imread(share1_path, cv2.IMREAD_GRAYSCALE)
        if stored_share1 is None:
            try:
                os.remove(share1_path)
            except OSError:
                pass
            emit('image_error', {'error': 'Could not verify stored Share 1'})
            return

        extra = {
            'share1_filename': share1_filename,
            'share1_token': share1_token,
            'share1_sha256': sha256_image(stored_share1),
            'share1_hmac': _share_hmac(stored_share1),
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

        # Sender never receives the token, so they cannot reconstruct.
        sender_payload = client_message(msg_obj_db, current_user['id'])
        for session_id in set(chat_room.get_sessions_for_user(current_user['id'])) | {request.sid}:
            emit('new_message', sender_payload, to=session_id)

        recipient_msg = client_message(msg_obj_db, recipient_id)
        recipient_msg['extra'] = dict(recipient_msg['extra'])
        recipient_msg['extra']['share2_live'] = f'data:image/png;base64,{share2_b64}'
        for session_id in recipient_sessions:
            emit('new_message', recipient_msg, to=session_id)
    except ValueError as exc:
        emit('image_error', {'error': str(exc)})
    except Exception as exc:
        print(f"Image share failed: {exc}")
        emit('image_error', {'error': 'Image could not be sent'})


@socketio.on('revoke_share')
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


@socketio.on('delete_account')
def handle_delete_account(data):
    """Delete user account and all their messages."""
    current_user = chat_room.get_session_user(request.sid)
    if not current_user:
        emit('auth_error', {'error': 'Not logged in'})
        return

    user_id = current_user['id']

    chat_room.delete_user_messages(user_id)
    chat_room.delete_user(user_id)
    chat_room.remove_user(request.sid)

    emit('logout_success', {}, to=request.sid)
    emit_users()
