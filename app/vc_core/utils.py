"""
Utility functions for Visual Cryptography
"""

import cv2
import os


def load_image(image_path):
    """Load image as grayscale"""
    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Could not load image: {image_path}")
    return image


def save_image(image, path):
    """Save image to specified path"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, image)
    return path


def resize_for_display(image, max_size=400):
    """Resize image maintaining aspect ratio for chat display"""
    h, w = image.shape
    if max(h, w) > max_size:
        scale = max_size / max(h, w)
        new_w = int(w * scale)
        new_h = int(h * scale)
        return cv2.resize(image, (new_w, new_h))
    return image
