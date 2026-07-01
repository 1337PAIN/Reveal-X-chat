"""Controlled tamper simulations for VC share testing."""

from __future__ import annotations

import cv2
import numpy as np

try:
    from .utils import ensure_gray
except ImportError:  # standalone training script
    from utils import ensure_gray


def tamper_share(image: np.ndarray, attack: str = "none", strength: float = 0.35, seed: int | None = None) -> np.ndarray:
    """Return a tampered copy of a share.

    attack values: none, block, noise, blur, brightness, crop, scratch, jpeg
    """
    rng = np.random.default_rng(seed)
    img = ensure_gray(image).copy()
    h, w = img.shape
    attack = (attack or "none").lower()
    if attack == "none":
        return img

    if attack == "block":
        bh = max(12, int(h * strength))
        bw = max(12, int(w * strength))
        y = int(rng.integers(0, max(1, h - bh + 1)))
        x = int(rng.integers(0, max(1, w - bw + 1)))
        value = int(rng.choice([0, 255, 32, 224]))
        img[y:y + bh, x:x + bw] = value
        return img

    if attack == "noise":
        mask = rng.random((h, w)) < min(0.75, max(0.01, strength))
        random_pixels = rng.integers(0, 256, size=(h, w), dtype=np.uint8)
        img[mask] = random_pixels[mask]
        return img

    if attack == "blur":
        bh = max(20, int(h * 0.45))
        bw = max(20, int(w * 0.45))
        y = int(rng.integers(0, max(1, h - bh + 1)))
        x = int(rng.integers(0, max(1, w - bw + 1)))
        patch = img[y:y + bh, x:x + bw]
        img[y:y + bh, x:x + bw] = cv2.GaussianBlur(patch, (17, 17), 0)
        return img

    if attack == "brightness":
        bh = max(20, int(h * 0.40))
        bw = max(20, int(w * 0.40))
        y = int(rng.integers(0, max(1, h - bh + 1)))
        x = int(rng.integers(0, max(1, w - bw + 1)))
        delta = int(rng.choice([-70, -45, 45, 70]))
        patch = img[y:y + bh, x:x + bw].astype(np.int16) + delta
        img[y:y + bh, x:x + bw] = np.clip(patch, 0, 255).astype(np.uint8)
        return img

    if attack == "crop":
        margin_y = max(1, int(h * strength * 0.35))
        margin_x = max(1, int(w * strength * 0.35))
        cropped = img[margin_y:h - margin_y or h, margin_x:w - margin_x or w]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

    if attack == "scratch":
        for _ in range(4):
            x1 = int(rng.integers(0, w))
            y1 = int(rng.integers(0, h))
            x2 = int(rng.integers(0, w))
            y2 = int(rng.integers(0, h))
            cv2.line(img, (x1, y1), (x2, y2), int(rng.choice([0, 255])), thickness=max(1, int(min(h, w) * 0.015)))
        return img

    if attack == "jpeg":
        quality = int(max(10, min(90, 100 - strength * 90)))
        ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            dec = cv2.imdecode(enc, cv2.IMREAD_GRAYSCALE)
            if dec is not None:
                return dec
        return img

    raise ValueError(f"Unsupported attack type: {attack}")
