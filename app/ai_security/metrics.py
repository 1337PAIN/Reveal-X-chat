"""Evaluation metrics for reconstructed/enhanced images."""

from __future__ import annotations

import math

import cv2
import numpy as np

try:
    from .utils import ensure_gray
except ImportError:  # standalone training script
    from utils import ensure_gray


def _match_size(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = ensure_gray(a)
    b = ensure_gray(b)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return a, b


def mse(a: np.ndarray, b: np.ndarray) -> float:
    a, b = _match_size(a, b)
    diff = a.astype(np.float64) - b.astype(np.float64)
    return float(np.mean(diff * diff))


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    value = mse(a, b)
    if value == 0:
        return 99.0
    return float(20 * math.log10(255.0 / math.sqrt(value)))


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Small dependency-free SSIM implementation for grayscale images."""
    a, b = _match_size(a, b)
    a = a.astype(np.float64)
    b = b.astype(np.float64)

    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2

    kernel = (11, 11)
    sigma = 1.5
    mu_a = cv2.GaussianBlur(a, kernel, sigma)
    mu_b = cv2.GaussianBlur(b, kernel, sigma)
    mu_a2 = mu_a * mu_a
    mu_b2 = mu_b * mu_b
    mu_ab = mu_a * mu_b

    sigma_a2 = cv2.GaussianBlur(a * a, kernel, sigma) - mu_a2
    sigma_b2 = cv2.GaussianBlur(b * b, kernel, sigma) - mu_b2
    sigma_ab = cv2.GaussianBlur(a * b, kernel, sigma) - mu_ab

    ssim_map = ((2 * mu_ab + c1) * (2 * sigma_ab + c2)) / ((mu_a2 + mu_b2 + c1) * (sigma_a2 + sigma_b2 + c2))
    return float(np.clip(ssim_map.mean(), 0.0, 1.0))
