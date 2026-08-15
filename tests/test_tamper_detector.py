"""ML tamper detection over VC shares."""

import os

import numpy as np
import pytest

from app.ai_security import TamperDetector
from app.ai_security.attacks import tamper_share
from app.ai_security.tamper_detector import FEATURE_NAMES, prediction_to_dict

# Attacks that leave a statistical trace the model can pick up. `noise` is
# deliberately absent - see test_noise_is_invisible_to_ml_but_caught_by_hmac.
STRUCTURAL_ATTACKS = ['block', 'blur', 'brightness', 'crop', 'scratch', 'jpeg']


@pytest.fixture(scope='module')
def detector():
    return TamperDetector()


@pytest.fixture(scope='module')
def fallback_detector():
    """The statistical scorer used when the trained model is unavailable."""
    return TamperDetector(model_path='__missing__.joblib')


def test_clean_shares_are_rarely_misclassified(detector):
    """False positives are a rate, not an absolute.

    The classifier misreads roughly 0.5% of genuine random shares as tampered,
    so asserting on a single sample would make this suite flake about once in
    200 runs. Assert the rate instead, with headroom.
    """
    samples = 30
    misclassified = sum(
        detector.predict(
            np.frombuffer(os.urandom(96 * 96), dtype=np.uint8).reshape((96, 96))
        ).tampered
        for _ in range(samples)
    )
    assert misclassified <= 2, f'{misclassified}/{samples} clean shares flagged as tampered'


def test_clean_share_prediction_is_well_formed(detector, random_share):
    prediction = detector.predict(random_share)
    assert prediction.label in ('Clean Share', 'Tampered Share')
    assert prediction.label == ('Tampered Share' if prediction.tampered else 'Clean Share')
    assert 0.0 <= prediction.probability_tampered <= 1.0
    assert prediction.tampered == (prediction.probability_tampered >= 0.5)


@pytest.mark.parametrize('attack', STRUCTURAL_ATTACKS)
def test_structural_attacks_are_detected(detector, seeded_share, attack):
    tampered = tamper_share(seeded_share, attack=attack, strength=0.4, seed=3)
    prediction = detector.predict(tampered)
    assert prediction.tampered is True, f'{attack} went undetected'
    assert prediction.probability_tampered >= 0.5


def test_noise_is_invisible_to_ml_but_caught_by_hmac(detector, seeded_share):
    """The documented limit of statistical tamper detection.

    Share 1 is uniform random, so overwriting pixels with more uniform random
    values changes nothing measurable: entropy, histogram and correlation all
    stay put. This is precisely why the exact HMAC check is not optional - it
    catches the case the classifier cannot.
    """
    from app.ai_security import hmac_image, verify_image

    tag = hmac_image(seeded_share, 'key')
    noisy = tamper_share(seeded_share, attack='noise', strength=0.4, seed=7)

    assert detector.predict(noisy).tampered is False   # ML cannot see it
    assert verify_image(noisy, 'key', tag) is False    # HMAC does


def test_fallback_scorer_works_without_a_model(fallback_detector, seeded_share):
    """A missing .joblib must degrade to the statistical scorer, not crash."""
    assert fallback_detector.model is None

    clean = fallback_detector.predict(seeded_share)
    tampered = fallback_detector.predict(
        tamper_share(seeded_share, attack='block', strength=0.4, seed=5)
    )

    assert clean.tampered is False
    assert tampered.tampered is True
    assert 'fallback' in clean.model_name.lower()


def test_feature_vector_shape_and_finiteness(detector, random_share):
    features = detector.extract_features(random_share)
    assert features.shape == (len(FEATURE_NAMES),)
    assert np.all(np.isfinite(features))


def test_clean_share_entropy_is_near_maximal(detector, random_share):
    features = dict(zip(FEATURE_NAMES, detector.extract_features(random_share)))
    # 8 bits is the ceiling for uniform bytes; real shares sit just under it.
    assert 7.5 < features['entropy'] <= 8.0
    assert abs(features['mean'] - 127.5) < 10


def test_heatmap_matches_input_dimensions(detector, random_share):
    heatmap = detector.generate_heatmap(random_share)
    assert heatmap.shape == random_share.shape
    assert heatmap.dtype == np.uint8


def test_heatmap_highlights_the_tampered_region(detector, random_share):
    """A flat block should score hotter than the untouched surroundings."""
    tampered = random_share.copy()
    tampered[10:40, 10:40] = 0

    heatmap = detector.generate_heatmap(tampered)
    inside = heatmap[12:38, 12:38].mean()
    outside = heatmap[60:90, 60:90].mean()
    assert inside > outside


def test_prediction_serialises_for_the_api(detector, random_share):
    payload = prediction_to_dict(detector.predict(random_share))

    for key in ('label', 'tampered', 'confidence', 'probability_tampered',
                'features', 'model_name', 'heatmap_data_url', 'explanation'):
        assert key in payload

    assert payload['heatmap_data_url'].startswith('data:image/png;base64,')
    assert isinstance(payload['explanation'], list) and payload['explanation']
    assert 0.5 <= payload['confidence'] <= 1.0
