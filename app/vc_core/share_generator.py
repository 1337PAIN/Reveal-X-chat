"""
(2,2) XOR Visual Cryptography Share Generator
"""

import cv2
import numpy as np
from .utils import load_image


class VisualCryptography:
    """
    Implements grayscale (2,2) XOR secret sharing.

    Share 1 is random noise. Share 2 is original XOR share 1.
    The original image is reconstructed by XORing both shares.
    """
    
    def __init__(self):
        """Initialize the XOR share generator"""
    
    def generate_shares_from_image(self, original):
        """
        Generate two XOR shares from an already-decoded grayscale image.

        Args:
            original: Grayscale image array

        Returns:
            share1, share2, original_image
        """
        h, w = original.shape
        if max(h, w) > 500:
            scale = 500 / max(h, w)
            original = cv2.resize(original, (int(w*scale), int(h*scale)))

        original = original.astype(np.uint8)
        share1 = np.random.randint(0, 256, size=original.shape, dtype=np.uint8)
        share2 = cv2.bitwise_xor(original, share1)

        return share1, share2, original

    def generate_shares(self, input_path, output_dir=None):
        """
        Generate two XOR shares from an input image without saving them.

        The caller is responsible for persisting only the share it is allowed
        to keep. Share 2 must not be written to server storage.
        """
        original = load_image(input_path)
        return self.generate_shares_from_image(original)
