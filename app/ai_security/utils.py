"""Image conversion helpers used by the AI security lab."""

from __future__ import annotations

import base64
from typing import Tuple

import cv2
import numpy as np

ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/bmp"}


def ensure_gray(image: np.ndarray) -> np.ndarray:
    """Return a uint8 grayscale image."""
    if image is None:
        raise ValueError("Image is empty")
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    return image


def data_url_to_image(data_url: str, max_bytes: int = 8 * 1024 * 1024) -> np.ndarray:
    """Decode a browser data URL into a grayscale OpenCV image."""
    if not data_url or "," not in data_url:
        raise ValueError("A valid base64 image data URL is required")
    header, encoded = data_url.split(",", 1)
    if not header.startswith("data:") or ";base64" not in header:
        raise ValueError("Only base64 image uploads are accepted")
    mime_type = header[5:].split(";", 1)[0].lower()
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError("Unsupported image type")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > max_bytes:
        raise ValueError("Image is too large")
    arr = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("Could not decode image")
    return image


def image_to_data_url(image: np.ndarray, ext: str = ".png") -> str:
    """Encode a grayscale/RGB image as a browser data URL."""
    image = ensure_gray(image) if image.ndim != 3 else image
    ok, buffer = cv2.imencode(ext, image)
    if not ok:
        raise ValueError("Could not encode image")
    encoded = base64.b64encode(buffer).decode("ascii")
    mime = "image/png" if ext.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{encoded}"


def resize_max(image: np.ndarray, max_side: int = 512) -> np.ndarray:
    """Resize image to a maximum side while preserving aspect ratio."""
    image = ensure_gray(image)
    h, w = image.shape[:2]
    if max(h, w) <= max_side:
        return image
    scale = max_side / max(h, w)
    return cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
