"""HTTP surface: the lab pipeline and the in-chat share integrity endpoint."""

import base64
import os

import cv2
import numpy as np
import pytest

from app import app as flask_app
from app.ai_security import hmac_image
from app.ai_security.attacks import tamper_share
from app.vc_core import VisualCryptography


def as_data_url(image):
    ok, buffer = cv2.imencode('.png', image)
    assert ok
    return 'data:image/png;base64,' + base64.b64encode(buffer).decode('ascii')


def store_share(store, sender, recipient, sample_image, token='tok-xyz'):
    """Persist a share pair and record Share 1's HMAC.

    The mask is seeded rather than taken from os.urandom so the ML verdict is
    reproducible: the classifier misreads a small fraction of genuine random
    shares, which would otherwise make these tests flake.
    """
    share1 = np.random.default_rng(20260822).integers(
        0, 256, size=sample_image.shape, dtype=np.uint8
    )

    filename = 'share1_route_test.png'
    path = os.path.join(store.shares_folder, filename)
    assert cv2.imwrite(path, share1)

    stored = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    message = store.add_message(
        sender['id'], recipient['id'], 'share', 'Shared an encrypted image',
        {
            'share1_filename': filename,
            'share1_token': token,
            'share1_hmac': hmac_image(stored, flask_app.config['SECRET_KEY']),
            'share1_sha256': 'recorded-at-send-time',
        },
    )
    return message, path


# ----------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------

def test_pages_render(client):
    assert client.get('/').status_code == 200
    assert client.get('/lab').status_code == 200


def test_security_headers_are_set(client):
    response = client.get('/')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']


def test_socketio_defaults_to_same_origin():
    """An unset REVEAL_X_ALLOWED_ORIGINS must not become a wildcard."""
    assert flask_app.config['ALLOWED_ORIGINS'] is None


# ----------------------------------------------------------------------
# /lab pipeline
# ----------------------------------------------------------------------

def test_lab_clean_run_passes_both_checks(client, sample_image):
    response = client.post('/api/lab/process',
                           json={'image': as_data_url(sample_image), 'attack': 'none'})
    assert response.status_code == 200

    data = response.get_json()
    assert data['ok'] is True
    # HMAC is exact, so these are deterministic. The ML verdict is not asserted
    # here: each run generates a fresh random share and the classifier has a
    # small false-positive rate, which is covered by
    # test_tamper_detector.test_clean_shares_are_rarely_misclassified.
    assert data['integrity']['hmac_match'] is True
    assert data['integrity']['exact_tamper_detected'] is False
    assert isinstance(data['ml']['tampered'], bool)
    # A clean share reconstructs exactly.
    assert data['metrics']['reconstructed']['ssim'] == 1.0
    assert set(data['images']) == {
        'original', 'share1_clean', 'share1_received', 'share2',
        'reconstructed', 'enhanced', 'heatmap',
    }


def test_lab_attack_is_caught_and_damages_reconstruction(client, sample_image):
    response = client.post('/api/lab/process',
                           json={'image': as_data_url(sample_image),
                                 'attack': 'block', 'strength': 0.4})
    data = response.get_json()

    assert data['integrity']['hmac_match'] is False
    assert data['integrity']['exact_tamper_detected'] is True
    assert data['ml']['tampered'] is True
    assert data['metrics']['reconstructed']['ssim'] < 1.0
    assert (data['integrity']['sha256_original_share1']
            != data['integrity']['sha256_received_share1'])


def test_lab_rejects_bad_input(client):
    assert client.post('/api/lab/process', json={'image': 'not-a-data-url'}).status_code == 400
    assert client.post('/api/lab/process',
                       json={'image': 'data:text/plain;base64,aGk='}).status_code == 400


# ----------------------------------------------------------------------
# In-chat share integrity analysis
# ----------------------------------------------------------------------

def test_analysis_verifies_an_untouched_share(client, store, users, sample_image):
    alice, bob = users
    message, _ = store_share(store, alice, bob, sample_image)

    response = client.get(f'/api/share/{message["id"]}/analysis?token=tok-xyz')
    assert response.status_code == 200

    data = response.get_json()
    assert data['ok'] is True
    assert data['integrity']['hmac_match'] is True
    assert data['integrity']['exact_tamper_detected'] is False
    assert data['ml']['tampered'] is False
    assert data['ml']['heatmap_data_url'].startswith('data:image/png;base64,')


def test_analysis_catches_a_share_modified_on_disk(client, store, users, sample_image):
    """The whole point: Share 1 is edited after it was stored, and we notice."""
    alice, bob = users
    message, path = store_share(store, alice, bob, sample_image)

    original = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    cv2.imwrite(path, tamper_share(original, attack='block', strength=0.4, seed=2))

    data = client.get(f'/api/share/{message["id"]}/analysis?token=tok-xyz').get_json()

    assert data['integrity']['hmac_match'] is False
    assert data['integrity']['exact_tamper_detected'] is True
    assert data['ml']['tampered'] is True


def test_analysis_requires_the_token(client, store, users, sample_image):
    alice, bob = users
    message, _ = store_share(store, alice, bob, sample_image)

    assert client.get(f'/api/share/{message["id"]}/analysis').status_code == 404
    assert client.get(f'/api/share/{message["id"]}/analysis?token=wrong').status_code == 404


def test_analysis_does_not_consume_one_time_access(client, store, users, sample_image):
    alice, bob = users
    message, _ = store_share(store, alice, bob, sample_image)

    for _ in range(3):
        assert client.get(f'/api/share/{message["id"]}/analysis?token=tok-xyz').status_code == 200

    # Share 1 itself is still downloadable exactly once afterwards.
    assert client.get(f'/share1/{message["id"]}?token=tok-xyz').status_code == 200


def test_analysis_reports_a_missing_file(client, store, users, sample_image):
    alice, bob = users
    message, path = store_share(store, alice, bob, sample_image)
    os.remove(path)

    response = client.get(f'/api/share/{message["id"]}/analysis?token=tok-xyz')
    assert response.status_code == 404
    assert response.get_json()['ok'] is False


# ----------------------------------------------------------------------
# Share 1 delivery
# ----------------------------------------------------------------------

def test_share1_is_served_once_then_gone(client, store, users, sample_image):
    alice, bob = users
    message, _ = store_share(store, alice, bob, sample_image)

    first = client.get(f'/share1/{message["id"]}?token=tok-xyz')
    assert first.status_code == 200
    assert first.mimetype == 'image/png'

    assert client.get(f'/share1/{message["id"]}?token=tok-xyz').status_code == 404


def test_share1_rejects_a_bad_token(client, store, users, sample_image):
    alice, bob = users
    message, _ = store_share(store, alice, bob, sample_image)

    assert client.get(f'/share1/{message["id"]}?token=nope').status_code == 404
    # A rejected attempt must not burn the recipient's real access.
    assert client.get(f'/share1/{message["id"]}?token=tok-xyz').status_code == 200


def test_downloaded_share_reconstructs_the_original(client, store, users, sample_image):
    """End-to-end: the bytes the recipient downloads XOR back to the secret."""
    alice, bob = users
    share1, share2, original = VisualCryptography().generate_shares_from_image(sample_image)

    filename = 'share1_e2e.png'
    path = os.path.join(store.shares_folder, filename)
    cv2.imwrite(path, share1)
    stored = cv2.imread(path, cv2.IMREAD_GRAYSCALE)

    message = store.add_message(
        alice['id'], bob['id'], 'share', 'Shared an encrypted image',
        {'share1_filename': filename, 'share1_token': 'tok-e2e',
         'share1_hmac': hmac_image(stored, flask_app.config['SECRET_KEY'])},
    )

    response = client.get(f'/share1/{message["id"]}?token=tok-e2e')
    downloaded = cv2.imdecode(np.frombuffer(response.data, np.uint8), cv2.IMREAD_GRAYSCALE)

    assert np.array_equal(cv2.bitwise_xor(downloaded, share2), original)


def test_unknown_message_id_is_not_found(client):
    assert client.get('/share1/does-not-exist?token=whatever').status_code == 404
    assert client.get('/api/share/does-not-exist/analysis?token=whatever').status_code == 404
