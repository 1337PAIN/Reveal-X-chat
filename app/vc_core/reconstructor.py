"""
Visual Cryptography Image Reconstruction
"""

import cv2
import numpy as np


class VCReconstructor:
    """Reconstruct original image from VC shares"""
    
    def reconstruct_overlay(self, share1, share2):
        """
        Reconstruct using overlay method (AND operation)
        Simulates physical stacking of shares
        """
        # Overlay: wherever both are white, result is white
        overlay = np.minimum(share1, share2)
        
        # Reverse pixel expansion (2x2 blocks -> 1 pixel)
        h, w = overlay.shape
        reconstructed = np.zeros((h//2, w//2), dtype=np.uint8)
        
        for i in range(0, h, 2):
            for j in range(0, w, 2):
                block = overlay[i:i+2, j:j+2]
                # If all pixels are black -> black pixel
                if np.all(block < 128):
                    reconstructed[i//2, j//2] = 0
                else:
                    reconstructed[i//2, j//2] = 255
        
        return reconstructed
    
    def reconstruct_xor(self, share1, share2):
        """
        Reconstruct using XOR method
        Digital reconstruction - better contrast
        """
        return cv2.bitwise_xor(share1, share2)
