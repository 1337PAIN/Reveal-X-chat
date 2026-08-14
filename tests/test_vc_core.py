"""Visual cryptography share generation and reconstruction."""

import numpy as np
import pytest

from app.vc_core import VisualCryptography, VCReconstructor


def test_xor_round_trip_is_lossless(sample_image):
    vc = VisualCryptography()
    share1, share2, original = vc.generate_shares_from_image(sample_image)

    reconstructed = VCReconstructor().reconstruct_xor(share1, share2)
    assert np.array_equal(reconstructed, original)


def test_neither_share_leaks_the_original(sample_image):
    """Each share on its own must look like noise, not like the secret."""
    vc = VisualCryptography()
    share1, share2, original = vc.generate_shares_from_image(sample_image)

    for share in (share1, share2):
        # A share that leaked structure would correlate with the original.
        correlation = np.corrcoef(share.ravel(), original.ravel())[0, 1]
        assert abs(correlation) < 0.1
        # Random bytes sit close to the middle of the range with high spread.
        assert 100 < share.mean() < 155
        assert share.std() > 60


def test_shares_differ_between_runs(sample_image):
    """The mask comes from os.urandom, so two runs never coincide."""
    vc = VisualCryptography()
    first, _, _ = vc.generate_shares_from_image(sample_image)
    second, _, _ = vc.generate_shares_from_image(sample_image)
    assert not np.array_equal(first, second)


def test_large_images_are_downscaled():
    vc = VisualCryptography()
    big = np.zeros((900, 1200), dtype=np.uint8)
    share1, share2, original = vc.generate_shares_from_image(big)

    assert max(original.shape) == 500
    assert share1.shape == original.shape == share2.shape


def test_reconstruct_rejects_mismatched_shares():
    reconstructor = VCReconstructor()
    with pytest.raises(ValueError):
        reconstructor.reconstruct_xor(
            np.zeros((10, 10), dtype=np.uint8),
            np.zeros((12, 12), dtype=np.uint8),
        )
