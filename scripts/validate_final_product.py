"""Quick validation for the final AI/ML modules without starting Flask."""

from __future__ import annotations

import sys
from pathlib import Path

import os

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "ai_security"))

from attacks import tamper_share
from enhancement import enhance_image
from metrics import psnr, ssim
from tamper_detector import TamperDetector

# Generate test shares, reconstruct the original image, and check for tampering.
def main():
    rng = np.random.default_rng(123)
    original = np.tile(np.arange(160, dtype=np.uint8), (160, 1))
    share1 = np.frombuffer(os.urandom(original.size), dtype=np.uint8).reshape(original.shape)
    share2 = cv2.bitwise_xor(original, share1)
    reconstructed = cv2.bitwise_xor(share1, share2)
    clean_pred = TamperDetector().predict(share1)

    tampered = tamper_share(share1, attack="block", strength=0.35, seed=7)
    tampered_pred = TamperDetector().predict(tampered)
    enhanced = enhance_image(reconstructed)

    print("Clean prediction:", clean_pred.label, round(clean_pred.probability_tampered, 4))
    print("Tampered prediction:", tampered_pred.label, round(tampered_pred.probability_tampered, 4))
    print("Reconstruction PSNR:", round(psnr(original, reconstructed), 4))
    print("Reconstruction SSIM:", round(ssim(original, reconstructed), 4))
    print("Enhanced shape:", enhanced.shape)

    assert reconstructed.shape == original.shape
    assert psnr(original, reconstructed) > 60
    assert clean_pred.probability_tampered < 0.5
    assert tampered_pred.probability_tampered >= 0.5
    print("Validation passed.")


if __name__ == "__main__":
    main()
