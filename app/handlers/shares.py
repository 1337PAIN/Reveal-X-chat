"""Image shares split in the sender's browser (send_share), and the
check that what was uploaded is plausibly Share 1.
"""

from app.handlers.common import *  # noqa: F401,F403


def _validate_share1_png(encoded):
    """Decode and check an uploaded Share 1. Returns (png_bytes, pixels).

    Raises ValueError with a message for the sender. The bytes are what gets
    stored -- unchanged, because the recipient checks their SHA-256 against
    the one the sender put inside the encrypted envelope.
    """
    if not isinstance(encoded, str) or not encoded:
        raise ValueError('Share 1 is missing')
    if len(encoded) > (MAX_SHARE1_BYTES * 4) // 3 + 4:
        raise ValueError('Share 1 is too large')
    try:
        png = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ValueError('Share 1 is not valid base64') from None
    if not png or len(png) > MAX_SHARE1_BYTES:
        raise ValueError('Share 1 is too large')
    # Read the header before handing anything to a decoder: the colour type
    # and size decide whether this is worth decoding at all.
    if png[:8] != b'\x89PNG\r\n\x1a\n' or png[12:16] != b'IHDR' or len(png) < 33:
        raise ValueError('Share 1 must be a PNG')
    width = int.from_bytes(png[16:20], 'big')
    height = int.from_bytes(png[20:24], 'big')
    depth, colour = png[24], png[25]
    if depth != 8 or colour != 0:
        raise ValueError('Share 1 must be an 8-bit single-channel PNG')
    if not (MIN_SHARE_SIDE <= width <= MAX_SHARE_SIDE and MIN_SHARE_SIDE <= height <= MAX_SHARE_SIDE):
        raise ValueError('Share 1 has unsupported dimensions')
    pixels = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    if pixels is None or pixels.ndim != 2 or pixels.dtype != np.uint8 \
            or pixels.shape != (height, width):
        raise ValueError('Share 1 could not be read')
    counts = np.bincount(pixels.ravel(), minlength=256).astype(np.float64)
    expected = pixels.size / 256.0
    if float(((counts - expected) ** 2 / expected).sum()) > SHARE_CHI_SQUARE_LIMIT:
        raise ValueError('That is not a share. Reload the page: images are split in your browser.')
    return png, pixels



@socketio.on('send_share')
@session_required
def handle_send_share(data):
    """Store a Share 1 the sender's browser made, plus Share 2 sealed for the
    recipient.

    The server never receives the image. vc-client.js splits it; Share 1
    (random bytes) arrives here as a single-channel PNG and is stored as sent;
    Share 2 travels inside `envelope`, an E2EE v2 ciphertext only the
    recipient can open, which also carries the SHA-256 of Share 1's bytes.
    Because the server cannot read Share 2 it may store it, so the recipient
    no longer has to be online. HMAC and the detector run on Share 1 as they
    always have.
    """
    data = data or {}
    current_user = chat_room.get_session_user(request.sid)
    recipient_id = data.get('recipient_id')
    if not chat_room.is_active_user(recipient_id):
        emit('image_error', {'error': 'Select a user first'})
        return
    if not _take_send_token(current_user['id'], 'image'):
        emit('image_error', {'error': SEND_TOO_FAST})
        return

    # Anything that looks like a plaintext image or a plaintext Share 2 means
    # a client that has not been updated, or one trying to put Share 2 where
    # the server could read it. Refused outright, not stripped.
    if 'image' in data or FORBIDDEN_EXTRA_KEYS & set(data):
        emit('image_error', {'error': 'Share 2 must be sent encrypted. Reload the page.'})
        return

    envelope = data.get('envelope')
    iv = data.get('iv') or ''
    if not isinstance(envelope, str) or not envelope or len(envelope) > MAX_SHARE_ENVELOPE_LENGTH:
        emit('image_error', {'error': 'The encrypted share is missing or too large'})
        return
    if not data.get('encrypted') or not iv or len(iv) > 64:
        emit('image_error', {'error': 'Invalid encrypted payload'})
        return
    envelope_fields, error = _e2ee_envelope(data, current_user['id'], recipient_id)
    if error:
        emit('image_error', {'error': error})
        return

    try:
        png, pixels = _validate_share1_png(data.get('share1_png'))
    except ValueError as exc:
        emit('image_error', {'error': str(exc)})
        return

    timestamp = utcnow().strftime('%Y%m%d_%H%M%S')
    share1_filename = f'share1_{timestamp}_{secrets.token_urlsafe(12)}.png'
    share1_path = os.path.join(app.config['SHARES_FOLDER'], share1_filename)

    def discard():
        try:
            os.remove(share1_path)
        except OSError:
            pass

    try:
        with open(share1_path, 'wb') as handle:
            handle.write(png)
        # Tag what is on disk, as before: the bytes the recipient downloads.
        stored = cv2.imread(share1_path, cv2.IMREAD_UNCHANGED)
        if stored is None or not np.array_equal(stored, pixels):
            discard()
            emit('image_error', {'error': 'Could not verify stored Share 1'})
            return

        extra = {
            'share1_filename': share1_filename,
            'share1_token': secrets.token_urlsafe(32),
            'share1_sha256': sha256_image(stored),
            'share1_hmac': _share_hmac(stored),
            'timestamp': timestamp,
            'encrypted': True,
            'iv': iv,
            'client_split': True,
        }
        extra.update(envelope_fields)
        reply_to_id = data.get('reply_to')
        if reply_to_id:
            extra['reply_to'] = reply_preview(
                chat_room.get_message_for_user(reply_to_id, current_user['id']))

        message = chat_room.add_message(current_user['id'], recipient_id, 'share', envelope, extra)
    except Exception as exc:
        discard()
        log.exception('Share upload failed: %s', exc)
        emit('image_error', {'error': 'Image could not be sent'})
        return
    if not message:
        discard()
        emit('image_error', {'error': DUPLICATE_MESSAGE_ERROR})
        return

    deliver_message(message, current_user['id'], recipient_id)
