"""Exact cryptographic integrity checks for visual-cryptography shares.

The ML detector answers "does this share look edited?". These helpers answer
"is this share bit-for-bit the one that was generated?". The lab and the chat
both record an HMAC at generation time and re-check it at retrieval time.
"""

from __future__ import annotations

import hashlib
import hmac

import numpy as np


def sha256_image(image: np.ndarray) -> str:
    """Content digest of a share, independent of any secret."""
    return hashlib.sha256(image.tobytes()).hexdigest()


def hmac_image(image: np.ndarray, key: str | bytes) -> str:
    """Keyed digest so an attacker who rewrites a share cannot forge the tag."""
    if isinstance(key, str):
        key = key.encode('utf-8')
    return hmac.new(key, image.tobytes(), hashlib.sha256).hexdigest()


def verify_image(image: np.ndarray, key: str | bytes, expected_hmac: str) -> bool:
    """Constant-time comparison of a share against its stored HMAC tag."""
    if not expected_hmac:
        return False
    return hmac.compare_digest(hmac_image(image, key), expected_hmac)
