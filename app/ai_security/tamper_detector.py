"""ML-based tamper detection for visual-cryptography shares.

The detector uses a lightweight scikit-learn model trained on statistical
features of valid random-looking VC shares and artificially tampered shares.
A deterministic anomaly score is also provided as a fallback so the app remains
usable even when the optional model file is missing.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List

import cv2
import numpy as np

try:
    from .utils import ensure_gray, image_to_data_url
except ImportError:  # standalone training script
    from utils import ensure_gray, image_to_data_url

try:
    import joblib
except Exception:  # pragma: no cover - optional dependency fallback
    joblib = None


FEATURE_NAMES = [
    "mean",
    "std",
    "entropy",
    "hist_chi2",
    "hist_l1_uniform",
    "corr_h",
    "corr_v",
    "laplacian_var",
    "block_mean_std",
    "block_std_mean",
    "unique_ratio",
    "zero_ratio",
    "sat_ratio",
]


@dataclass
class Prediction:
    label: str
    tampered: bool
    confidence: float
    probability_tampered: float
    features: Dict[str, float]
    model_name: str
    heatmap_data_url: str
    explanation: List[str]


class TamperDetector:
    """Runtime wrapper for the trained tamper detection model."""

    def __init__(self, model_path: str | None = None):
        default_path = os.path.join(os.path.dirname(__file__), "models", "tamper_detector.joblib")
        self.model_path = model_path or default_path
        self.model = None
        self.model_name = "Statistical fallback model"
        if joblib and os.path.isfile(self.model_path):
            try:
                payload = joblib.load(self.model_path)
                if isinstance(payload, dict) and "model" in payload:
                    self.model = payload["model"]
                    self.model_name = payload.get("model_name", "RandomForest tamper detector")
                else:
                    self.model = payload
                    self.model_name = "RandomForest tamper detector"
            except Exception:
                self.model = None

    def extract_features(self, image: np.ndarray) -> np.ndarray:
        return self._features(ensure_gray(image))[0]

    def _features(self, image: np.ndarray) -> tuple[np.ndarray, tuple]:
        """The feature vector, plus the 16-px block grid it was built from so
        predict() can hand that grid to the heatmap instead of rebuilding it."""
        flat = image.ravel().astype(np.float64)
        hist = np.bincount(image.ravel(), minlength=256).astype(np.float64)
        total = max(float(flat.size), 1.0)
        probs = hist / total
        uniform = np.full(256, 1 / 256, dtype=np.float64)

        entropy = -float(np.sum(probs[probs > 0] * np.log2(probs[probs > 0])))
        expected = total / 256.0
        hist_chi2 = float(np.mean(((hist - expected) ** 2) / (expected + 1e-9)))
        hist_l1 = float(np.sum(np.abs(probs - uniform)))

        if image.shape[1] > 1:
            corr_h = self._safe_corr(image[:, :-1].ravel(), image[:, 1:].ravel())
        else:
            corr_h = 0.0
        if image.shape[0] > 1:
            corr_v = self._safe_corr(image[:-1, :].ravel(), image[1:, :].ravel())
        else:
            corr_v = 0.0

        lap_var = float(cv2.Laplacian(image, cv2.CV_64F).var())
        blocks = self._block_grid(image, block=16)
        block_means, block_stds, _ = blocks
        # Distinct grey levels: the histogram already has them, and np.unique
        # would sort all 1.44 M pixels of a full-size share to find the same.
        unique_ratio = float(np.count_nonzero(hist) / 256.0)
        zero_ratio = float(np.mean(image == 0))
        sat_ratio = float(np.mean(image == 255))

        return np.array([
            float(flat.mean()),
            float(flat.std()),
            entropy,
            hist_chi2,
            hist_l1,
            corr_h,
            corr_v,
            lap_var,
            float(np.std(block_means)) if block_means.size else 0.0,
            float(np.mean(block_stds)) if block_stds.size else 0.0,
            unique_ratio,
            zero_ratio,
            sat_ratio,
        ], dtype=np.float64), blocks

    def predict(self, image: np.ndarray) -> Prediction:
        image = ensure_gray(image)
        # Extract image features used to detect possible tampering.
        features, blocks = self._features(image)
        probability_tampered = self._fallback_probability(features)
        model_name = self.model_name

        if self.model is not None:
            try:
                if hasattr(self.model, "predict_proba"):
                    probability_tampered = float(self.model.predict_proba([features])[0][1])
                else:
                    pred = int(self.model.predict([features])[0])
                    probability_tampered = 0.9 if pred else 0.1
            except Exception:
                model_name = "Statistical fallback model"
                probability_tampered = self._fallback_probability(features)

        tampered = probability_tampered >= 0.5
        heatmap = self.generate_heatmap(image, global_std=float(features[1]), _blocks=blocks)
        explanation = self.explain(features, probability_tampered)
        return Prediction(
            label="Tampered Share" if tampered else "Clean Share",
            tampered=tampered,
            confidence=float(max(probability_tampered, 1 - probability_tampered)),
            probability_tampered=float(probability_tampered),
            features={name: float(value) for name, value in zip(FEATURE_NAMES, features)},
            model_name=model_name,
            heatmap_data_url=image_to_data_url(heatmap),
            explanation=explanation,
        )

    def generate_heatmap(self, image: np.ndarray, grid: int = 16,
                         global_std: float | None = None, _blocks: tuple | None = None) -> np.ndarray:
        """Generate a visual suspicious-region heatmap as a grayscale image.

        Each grid block is scored on its own entropy, mean, spread and share
        of pure black/white pixels. All blocks are scored at once with NumPy:
        a Python loop over the 5,625 blocks of a 1,200-px share took ~400 ms.
        ``global_std`` is the image's standard deviation; predict() passes the
        one it already has instead of extracting every feature again.
        """
        image = ensure_gray(image)
        h, w = image.shape
        if h == 0 or w == 0:
            return np.zeros((h, w), dtype=np.uint8)
        if global_std is None:
            global_std = float(image.astype(np.float64).std())
        global_std = max(global_std, 1.0)

        if _blocks is None or grid != 16:
            _blocks = self._block_grid(image, block=grid)
        means, stds, counts = _blocks
        nby, nbx = means.shape
        rows = np.arange(h) // grid
        cols = np.arange(w) // grid
        block_id = rows[:, None] * nbx + cols[None, :]
        hist = np.bincount((block_id * 256 + image).ravel(),
                           minlength=nby * nbx * 256).reshape(nby, nbx, 256)
        probs = hist / counts[..., None]
        logp = np.zeros_like(probs)
        np.log2(probs, out=logp, where=probs > 0)
        entropy = -np.sum(probs * logp, axis=2)

        # Patches with very low entropy or large mean/std deviation are suspicious.
        score = np.maximum(0.0, (7.4 - entropy) / 2.0)
        score += np.minimum(1.0, np.abs(means - 127.5) / 80.0)
        score += np.minimum(1.0, np.abs(stds - global_std) / 60.0)
        clipped = (hist[..., 0] / counts > 0.05) | (hist[..., 255] / counts > 0.05)
        score += np.where(clipped, 0.6, 0.0)
        score = np.minimum(score, 2.5)
        score[counts < 16] = 0.0  # slivers too small to judge stay cold

        heat = score.astype(np.float32)
        if heat.max() > 0:
            heat = heat / heat.max()
        block_u8 = (heat * 255).astype(np.uint8)
        heat_u8 = block_u8[rows[:, None], cols[None, :]]
        heat_u8 = cv2.GaussianBlur(heat_u8, (0, 0), 3)
        return heat_u8

    def explain(self, features: np.ndarray, probability: float) -> List[str]:
        named = {name: float(value) for name, value in zip(FEATURE_NAMES, features)}
        reasons = []
        if named["entropy"] < 7.6:
            reasons.append("Entropy is lower than a valid random VC share, suggesting inserted structure or edited pixels.")
        if abs(named["mean"] - 127.5) > 8:
            reasons.append("Pixel mean is shifted away from the expected random-share centre.")
        if named["hist_l1_uniform"] > 0.25:
            reasons.append("Histogram distribution deviates from uniform random noise.")
        if abs(named["corr_h"]) > 0.05 or abs(named["corr_v"]) > 0.05:
            reasons.append("Neighbouring pixels show unusual correlation for a random share.")
        if named["zero_ratio"] > 0.02 or named["sat_ratio"] > 0.02:
            reasons.append("Large numbers of pure black/white pixels indicate block replacement or clipping.")
        if not reasons:
            reasons.append("Statistical pattern is consistent with a clean random-looking VC share.")
        reasons.append(f"Model probability of tampering: {probability * 100:.1f}%.")
        return reasons[:5]

    @staticmethod
    def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
        """Pearson correlation, 0 when either side is constant.

        Computed directly with two dot products: np.corrcoef stacks and
        copies both 1.4 M-pixel vectors and took ~30 ms per call at 1,200 px.
        """
        if a.size < 2:
            return 0.0
        a = a.astype(np.float64)
        b = b.astype(np.float64)
        a -= a.mean()
        b -= b.mean()
        saa = float(np.dot(a, a))
        sbb = float(np.dot(b, b))
        if saa == 0.0 or sbb == 0.0:
            return 0.0
        return float(np.dot(a, b) / math.sqrt(saa * sbb))

    @staticmethod
    def _block_grid(image: np.ndarray, block: int = 16) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Mean, standard deviation and pixel count of every block, as grids.

        Edge blocks are whatever is left over, as with slicing. Sums of pixels
        and of their squares are taken in integers, which is exact, so the
        variance (n*Q - S^2) / n^2 is rounded once and matches ndarray.std to
        the last few bits: the model sees the features it was trained on.
        """
        h, w = image.shape
        if h == 0 or w == 0:
            empty = np.zeros((0, 0), dtype=np.float64)
            return empty, empty, empty
        ys = np.arange(0, h, block)
        xs = np.arange(0, w, block)
        pixels = image.astype(np.int64)

        def block_sum(values: np.ndarray) -> np.ndarray:
            return np.add.reduceat(np.add.reduceat(values, ys, axis=0), xs, axis=1)

        n = np.outer(np.diff(np.append(ys, h)), np.diff(np.append(xs, w)))
        s = block_sum(pixels)
        q = block_sum(pixels * pixels)
        counts = n.astype(np.float64)
        means = s / counts
        stds = np.sqrt((n * q - s * s) / (counts * counts))
        return means, stds, counts

    @staticmethod
    def _fallback_probability(features: np.ndarray) -> float:
        named = {name: float(value) for name, value in zip(FEATURE_NAMES, features)}
        score = 0.0
        score += max(0.0, 7.75 - named["entropy"]) * 1.6
        score += max(0.0, abs(named["mean"] - 127.5) - 6) / 15
        score += max(0.0, named["hist_l1_uniform"] - 0.18) * 2.5
        score += max(0.0, abs(named["corr_h"]) - 0.04) * 5
        score += max(0.0, abs(named["corr_v"]) - 0.04) * 5
        score += max(0.0, named["zero_ratio"] - 0.015) * 15
        score += max(0.0, named["sat_ratio"] - 0.015) * 15
        # Logistic squashing. 0.8 is the approximate decision midpoint.
        return float(1.0 / (1.0 + math.exp(-(score - 0.8) * 2.2)))


def prediction_to_dict(prediction: Prediction) -> Dict[str, Any]:
    return {
        "label": prediction.label,
        "tampered": prediction.tampered,
        "confidence": prediction.confidence,
        "probability_tampered": prediction.probability_tampered,
        "features": prediction.features,
        "model_name": prediction.model_name,
        "heatmap_data_url": prediction.heatmap_data_url,
        "explanation": prediction.explanation,
    }
