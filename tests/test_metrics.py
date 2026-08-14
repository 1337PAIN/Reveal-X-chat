"""MSE / PSNR / SSIM behaviour."""

import numpy as np

from app.ai_security import enhance_image, mse, psnr, ssim


def test_identical_images_score_perfectly(sample_image):
    assert mse(sample_image, sample_image) == 0.0
    assert psnr(sample_image, sample_image) == 99.0
    assert ssim(sample_image, sample_image) == 1.0


def test_damage_lowers_every_score(sample_image):
    damaged = sample_image.copy()
    damaged[10:60, 10:60] = 0

    assert mse(sample_image, damaged) > 0
    assert psnr(sample_image, damaged) < 99.0
    assert ssim(sample_image, damaged) < 1.0


def test_worse_damage_scores_worse(sample_image):
    light = sample_image.copy()
    light[10:20, 10:20] = 0
    heavy = sample_image.copy()
    heavy[10:80, 10:80] = 0

    assert mse(sample_image, heavy) > mse(sample_image, light)
    assert psnr(sample_image, heavy) < psnr(sample_image, light)
    assert ssim(sample_image, heavy) < ssim(sample_image, light)


def test_metrics_resize_mismatched_inputs(sample_image):
    smaller = sample_image[::2, ::2]
    assert 0.0 <= ssim(sample_image, smaller) <= 1.0


def test_ssim_stays_in_range(sample_image):
    noise = np.frombuffer(bytes(range(256)) * 36, dtype=np.uint8).reshape((96, 96))
    assert 0.0 <= ssim(sample_image, noise) <= 1.0


def test_enhancement_preserves_shape_and_dtype(sample_image):
    enhanced = enhance_image(sample_image)
    assert enhanced.shape == sample_image.shape
    assert enhanced.dtype == np.uint8
