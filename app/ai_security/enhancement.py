"""Classical image enhancement for reconstructed Reveal-X images.

This is **not** a learned model. It is a fixed three-stage OpenCV pipeline --
non-local means denoising, CLAHE contrast normalisation, and an unsharp mask --
chosen because it needs no GPU, no training data and no inference dependency,
and because XOR reconstruction is lossless anyway: on a clean share there is
nothing to restore, so enhancement only matters on a damaged one.

A CNN denoiser trained on a VC-specific dataset was scoped for this project and
is **not implemented**; see the milestone status table in README.md. The metrics
harness in metrics.py is the part that would let one be evaluated fairly, since
it scores original-vs-reconstructed and original-vs-enhanced side by side --
and, notably, records when enhancement makes SSIM *worse*, which this pipeline
sometimes does.
"""

from __future__ import annotations

import cv2
import numpy as np

try:
    from .utils import ensure_gray
except ImportError:  # standalone training script
    from utils import ensure_gray


def enhance_image(image: np.ndarray) -> np.ndarray:
    """Enhance a reconstructed grayscale image.

    The function returns a visually cleaner image while preserving dimensions.
    For FYP evaluation, compare original-vs-reconstructed and original-vs-
    enhanced using PSNR and SSIM.
    """
    gray = ensure_gray(image)
    # Non-local means denoising reduces VC reconstruction speckle.
    denoised = cv2.fastNlMeansDenoising(gray, None, h=7, templateWindowSize=7, searchWindowSize=21)
    # CLAHE improves local contrast for document/text images.
    clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
    contrast = clahe.apply(denoised)
    # Gentle unsharp mask restores edges without extreme artifacts.
    blur = cv2.GaussianBlur(contrast, (0, 0), 1.0)
    sharpened = cv2.addWeighted(contrast, 1.35, blur, -0.35, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)
