"""HTTP routes, the static/asset helpers, and the request hooks
(retention sweeper start, HTTPS redirect, security headers).
"""

from app.handlers.common import *  # noqa: F401,F403


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
    wait = _lab_rate_limited(_client_address())
    if wait:
        return jsonify({'ok': False,
                        'error': f'Too many requests. Try again in {wait}s.'}), 429
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


@app.template_global()
def asset(filename):
    """Static URL with a cache-buster derived from the file itself.

    The version used to be a literal typed into the template -- ?v=admin1 --
    which meant every edit needed a matching hand-edit somewhere else. Miss it
    and returning browsers keep serving the old file: the code is deployed,
    the tests pass, and the user sees none of it. That happened twice.

    The modification time removes the coupling. It changes exactly when the
    file does, and never when it does not.
    """
    try:
        stamp = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
    except OSError:
        stamp = 0
    return f"{url_for('static', filename=filename)}?v={stamp}"




@app.route('/api/lab/process', methods=['POST'])
def process_ai_lab_image():
    """Generate VC shares, simulate tampering, run ML detection, reconstruct, enhance and score."""
    started = time.perf_counter()
    wait = _lab_rate_limited(_client_address())
    if wait:
        return jsonify({'ok': False,
                        'error': f'Too many requests. Try again in {wait}s.'}), 429
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
        log.exception('AI lab processing failed: %s', exc)
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

# 🔒 Securely retrieve Share 1 using a token and enforce one-time access.
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


def _request_is_https():
    """Did this request arrive over HTTPS?

    X-Forwarded-Proto is believed only with REVEALX_TRUST_PROXY, for the same
    reason X-Forwarded-For is: without a proxy that sets it, it is whatever the
    client sent. It used to be read unconditionally, so a client on plain HTTP
    could claim HTTPS and be sent HSTS -- and behind a TLS-terminating proxy
    the app was not told to trust, REVEAL_X_FORCE_HTTPS saw every request as
    http:// and redirected for ever.
    """
    if request.is_secure:
        return True
    if os.environ.get('REVEALX_TRUST_PROXY', '').lower() == 'true':
        proto = request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip().lower()
        return proto == 'https'
    return False


@app.before_request
def start_retention_sweeper():
    """Lazily, so importing the app (scripts, tools) starts no thread."""
    sweeper.ensure_started(chat_room)


@app.before_request
def redirect_plain_http():
    """REVEAL_X_FORCE_HTTPS: send plain-HTTP requests to HTTPS.

    Before the request is handled, not after. This used to run in
    after_request, so a plain-HTTP POST was processed in full -- lab
    inference, a share upload -- and only then answered with a redirect.
    308 for anything but GET/HEAD, so a client that follows it re-sends the
    same method and body rather than turning a POST into a GET.
    """
    if os.environ.get('REVEAL_X_FORCE_HTTPS', '').lower() != 'true' or _request_is_https():
        return None
    code = 301 if request.method in ('GET', 'HEAD') else 308
    return redirect(request.url.replace('http://', 'https://', 1), code=code)


@app.after_request
def add_security_headers(response):
    # HSTS only over HTTPS: browsers ignore it on plain HTTP, and localhost
    # stays easy to run.
    if _request_is_https():
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    # HTML carries no cache headers of its own, so browsers fall back to
    # heuristic caching and may serve the page from cache without asking. The
    # assets are fine either way -- they are revalidated and versioned -- but a
    # cached *page* still lists the old ones, so a newly added script tag never
    # appears and the fix looks like it did not work until a hard refresh.
    #
    # no-cache does not mean "do not store"; it means "ask before reusing", so
    # an unchanged page still costs only a 304.
    if response.mimetype == 'text/html':
        response.headers['Cache-Control'] = 'no-cache'
    # The chat modules import each other by plain relative URL, without the
    # ?v= stamp asset() puts on the entry point, so a heuristically cached
    # module could be paired with a newer one that expects different exports.
    # Revalidating every time keeps the set consistent at the cost of a 304.
    if request.path.startswith('/static/js/chat/') or request.path == '/static/js/admin.js':
        response.headers['Cache-Control'] = 'no-cache'

    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    # microphone=(self), not (): an empty allowlist disables the microphone for
    # the app's own origin too, so getUserMedia failed before any prompt and
    # voice notes could not be recorded in browsers that enforce this header.
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(self), geolocation=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        # www.gstatic.com serves the Firebase SDK; apis.google.com and the
        # identitytoolkit/securetoken endpoints are what Firebase Auth talks to.
        # 'wasm-unsafe-eval' lets onnxruntime-web compile the tamper-detection
        # model. It permits WebAssembly compilation only -- unlike
        # 'unsafe-eval' it does not re-enable eval() or new Function().
        # socket.io, DOMPurify, Font Awesome and Inter are all served from
        # /static/vendor now, so their CDNs are no longer allowed: an origin in
        # the policy that nothing loads from is an origin an injected tag can.
        "script-src 'self' 'wasm-unsafe-eval' "
        "https://www.gstatic.com https://apis.google.com; "
        # Inline *styles* remain: the markup and the scripts set style
        # attributes throughout. Inline *scripts* are not allowed.
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
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
