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
