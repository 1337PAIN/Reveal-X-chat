"""SHA-256 / HMAC-SHA256 exact integrity verification for shares."""

import numpy as np

from app.ai_security import hmac_image, sha256_image, verify_image
from app.ai_security.attacks import tamper_share

KEY = 'test-secret-key'


def test_hmac_verifies_an_untouched_share(random_share):
    tag = hmac_image(random_share, KEY)
    assert verify_image(random_share, KEY, tag) is True


def test_hmac_detects_a_single_flipped_pixel(random_share):
    tag = hmac_image(random_share, KEY)

    modified = random_share.copy()
    modified[0, 0] = (int(modified[0, 0]) + 1) % 256

    assert verify_image(modified, KEY, tag) is False


def test_hmac_detects_every_simulated_attack(random_share):
    tag = hmac_image(random_share, KEY)

    for attack in ('block', 'noise', 'blur', 'brightness', 'crop', 'scratch', 'jpeg'):
        tampered = tamper_share(random_share, attack=attack, strength=0.35, seed=11)
        assert verify_image(tampered, KEY, tag) is False, attack


def test_hmac_needs_the_right_key(random_share):
    tag = hmac_image(random_share, KEY)
    assert verify_image(random_share, 'a-different-key', tag) is False


def test_missing_tag_never_verifies(random_share):
    assert verify_image(random_share, KEY, '') is False


def test_sha256_is_stable_and_content_bound(random_share):
    assert sha256_image(random_share) == sha256_image(random_share.copy())

    other = random_share.copy()
    other[5, 5] = (int(other[5, 5]) + 7) % 256
    assert sha256_image(other) != sha256_image(random_share)


def test_no_attack_leaves_the_share_untouched(random_share):
    assert np.array_equal(tamper_share(random_share, attack='none'), random_share)
