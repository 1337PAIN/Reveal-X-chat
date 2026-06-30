"""
Visual Cryptography Image Reconstruction
"""

import cv2


class VCReconstructor:
    """Reconstruct the original image from a pair of (2,2) XOR shares."""

    def reconstruct_xor(self, share1, share2):
        """Reconstruct the secret image.

        The share generator builds Share 1 as a random mask and Share 2 as
        `original XOR share1`, so XORing the pair returns the original exactly.
        """
        if share1.shape != share2.shape:
            raise ValueError('Shares must have the same dimensions')
        return cv2.bitwise_xor(share1, share2)
