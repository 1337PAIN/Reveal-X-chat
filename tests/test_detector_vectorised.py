"""The vectorised detector must compute exactly what the per-block loops did.

Detection at the 1,200-px chat cap took about 480 ms, almost all of it in two
Python loops over 16 x 16 blocks (5,625 of them): one for the block features,
one for the heatmap, which also recomputed every feature a second time. Both
were replaced with whole-image NumPy operations. The trained model was fitted
on the loop's numbers, so the replacement is only safe if it reproduces them;
the loop versions are kept here verbatim as the reference.
"""

import numpy as np
import pytest
import cv2

from app.ai_security import TamperDetector
from app.ai_security.attacks import tamper_share


def _reference_block_stats(image, block=16):
    means, stds = [], []
    h, w = image.shape
    for y in range(0, h, block):
        for x in range(0, w, block):
            patch = image[y:y + block, x:x + block]
            if patch.size:
                means.append(float(patch.mean()))
                stds.append(float(patch.std()))
    return means, stds


def _reference_heatmap(image, global_std, grid=16):
    h, w = image.shape
    heat = np.zeros((h, w), dtype=np.float32)
    for y in range(0, h, grid):
        for x in range(0, w, grid):
            patch = image[y:y + grid, x:x + grid]
            if patch.size < 16:
                continue
            ph = np.bincount(patch.ravel(), minlength=256).astype(np.float64)
            probs = ph / max(float(patch.size), 1.0)
            entropy = -float(np.sum(probs[probs > 0] * np.log2(probs[probs > 0])))
            local_std = float(patch.std())
            local_mean = float(patch.mean())
            score = 0.0
            score += max(0.0, (7.4 - entropy) / 2.0)
            score += min(1.0, abs(local_mean - 127.5) / 80.0)
            score += min(1.0, abs(local_std - global_std) / 60.0)
            if np.mean(patch == 0) > 0.05 or np.mean(patch == 255) > 0.05:
                score += 0.6
            heat[y:y + grid, x:x + grid] = min(score, 2.5)
    if heat.max() > 0:
        heat = heat / heat.max()
    heat_u8 = (heat * 255).astype(np.uint8)
    return cv2.GaussianBlur(heat_u8, (0, 0), 3)


def _images():
    rng = np.random.default_rng(1115)
    clean = rng.integers(0, 256, (240, 240), dtype=np.uint8)
    yield 'clean', clean
    # Sizes that are not multiples of 16 leave ragged edge blocks, and a
    # sliver narrower than 16 px makes patches the heatmap must skip.
    yield 'ragged', rng.integers(0, 256, (97, 131), dtype=np.uint8)
    yield 'sliver', rng.integers(0, 256, (37, 3), dtype=np.uint8)
    yield 'tiny', rng.integers(0, 256, (5, 5), dtype=np.uint8)
    yield 'flat', np.full((64, 80), 200, dtype=np.uint8)
    for attack in ('block', 'blur', 'brightness', 'scratch', 'jpeg'):
        yield attack, tamper_share(clean, attack=attack, strength=0.4, seed=3)


IMAGES = list(_images())


@pytest.fixture(scope='module')
def detector():
    return TamperDetector()


@pytest.mark.parametrize('name,image', IMAGES, ids=[n for n, _ in IMAGES])
def test_block_features_match_the_loop(detector, name, image):
    means, stds = _reference_block_stats(image)
    features = detector.extract_features(image)
    assert features[8] == pytest.approx(float(np.std(means)), rel=1e-9, abs=1e-9)
    assert features[9] == pytest.approx(float(np.mean(stds)), rel=1e-9, abs=1e-9)


@pytest.mark.parametrize('name,image', IMAGES, ids=[n for n, _ in IMAGES])
def test_unique_ratio_matches_np_unique(detector, name, image):
    assert detector.extract_features(image)[10] == np.unique(image).size / 256.0


@pytest.mark.parametrize('name,image', IMAGES, ids=[n for n, _ in IMAGES])
def test_heatmap_matches_the_loop(detector, name, image):
    global_std = max(detector.extract_features(image)[1], 1.0)
    expected = _reference_heatmap(image, global_std)
    actual = detector.generate_heatmap(image)
    assert actual.shape == expected.shape
    # Float summation order differs, so a block score can land on the other
    # side of a uint8 rounding step; allow one grey level, never more.
    assert int(np.abs(actual.astype(int) - expected.astype(int)).max()) <= 1


def test_prediction_is_unchanged_on_a_full_size_share(detector):
    rng = np.random.default_rng(7)
    share = rng.integers(0, 256, (1200, 1200), dtype=np.uint8)
    tampered = tamper_share(share, attack='block', strength=0.4, seed=3)
    for image in (share, tampered):
        features = detector.extract_features(image)
        means, stds = _reference_block_stats(image)
        assert features[8] == pytest.approx(float(np.std(means)), rel=1e-9)
        assert features[9] == pytest.approx(float(np.mean(stds)), rel=1e-9)
    assert detector.predict(tampered).tampered


@pytest.mark.parametrize('name,image', IMAGES, ids=[n for n, _ in IMAGES])
def test_neighbour_correlation_matches_corrcoef(detector, name, image):
    features = detector.extract_features(image)
    for got, (a, b) in zip(features[5:7], (
        (image[:, :-1].ravel(), image[:, 1:].ravel()),
        (image[:-1, :].ravel(), image[1:, :].ravel()),
    )):
        a = a.astype(np.float64)
        b = b.astype(np.float64)
        if a.size < 2 or a.std() == 0 or b.std() == 0:
            assert got == 0.0
        else:
            assert got == pytest.approx(float(np.corrcoef(a, b)[0, 1]), rel=1e-9, abs=1e-12)
