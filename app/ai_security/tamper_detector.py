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
        image = ensure_gray(image)
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
        block_means, block_stds = self._block_stats(image, block=16)
        unique_ratio = float(np.unique(image).size / 256.0)
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
            float(np.std(block_means)) if block_means else 0.0,
            float(np.mean(block_stds)) if block_stds else 0.0,
            unique_ratio,
            zero_ratio,
            sat_ratio,
        ], dtype=np.float64)

    def predict(self, image: np.ndarray) -> Prediction:
        image = ensure_gray(image)
        features = self.extract_features(image)
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
        heatmap = self.generate_heatmap(image)
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

    def generate_heatmap(self, image: np.ndarray, grid: int = 16) -> np.ndarray:
        """Generate a visual suspicious-region heatmap as a grayscale image."""
        image = ensure_gray(image)
        h, w = image.shape
        heat = np.zeros((h, w), dtype=np.float32)
        global_features = self.extract_features(image)
        global_std = max(global_features[1], 1.0)

        for y in range(0, h, grid):
            for x in range(0, w, grid):
                patch = image[y:y + grid, x:x + grid]
                if patch.size < 16:
                    continue
                ph = np.bincount(patch.ravel(), minlength=256).astype(np.float64)
                probs = ph / max(float(patch.size), 1.0)
                entropy = -float(np.sum(probs[probs > 0] * np.log2(probs[probs > 0])))
                local_std = float(patch.std())
                local_mean = float(patch.mean())
                # Patches with very low entropy or large mean/std deviation are suspicious.
                score = 0.0
                score += max(0.0, (7.4 - entropy) / 2.0)
                score += min(1.0, abs(local_mean - 127.5) / 80.0)
                score += min(1.0, abs(local_std - global_std) / 60.0)
                if np.mean(patch == 0) > 0.05 or np.mean(patch == 255) > 0.05:
                    score += 0.6
                heat[y:y + grid, x:x + grid] = min(score, 2.5)

        if heat.max() > 0:
            heat = heat / heat.max()
        heat_u8 = (heat * 255).astype(np.uint8)
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
        a = a.astype(np.float64)
        b = b.astype(np.float64)
        if a.size < 2 or a.std() == 0 or b.std() == 0:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    @staticmethod
    def _block_stats(image: np.ndarray, block: int = 16) -> tuple[list[float], list[float]]:
        means: list[float] = []
        stds: list[float] = []
        h, w = image.shape
        for y in range(0, h, block):
            for x in range(0, w, block):
                patch = image[y:y + block, x:x + block]
                if patch.size:
                    means.append(float(patch.mean()))
                    stds.append(float(patch.std()))
        return means, stds

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
