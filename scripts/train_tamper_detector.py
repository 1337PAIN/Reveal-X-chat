"""Train the Reveal-X ML tamper detector.

This script creates a synthetic but controlled dataset of visual-cryptography
shares. Clean examples are random-looking VC Share 1 images. Tampered examples
are created with realistic edit operations: block replacement, noise, blur,
brightness shift, crop-resize, scratches, and JPEG damage.

Run:
    python scripts/train_tamper_detector.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cv2
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "ai_security"))

from attacks import tamper_share
from tamper_detector import FEATURE_NAMES, TamperDetector


def build_dataset(samples: int = 120, size: int = 96, seed: int = 42):
    rng = np.random.default_rng(seed)
    detector = TamperDetector(model_path="__missing__.joblib")
    attacks = ["block", "noise", "blur", "brightness", "crop", "scratch", "jpeg"]
    X, y = [], []

    for i in range(samples):
        # Share 1 is a uniform random mask, so it does not depend on the image
        # it will hide. An earlier version generated a synthetic "original"
        # here and claimed the shares therefore came from varied images; they
        # did not -- only its .shape was ever read. Saying so plainly is worth
        # more than the shapes it drew.
        share1 = rng.integers(0, 256, size=(size, size), dtype=np.uint8)
        X.append(detector.extract_features(share1))
        y.append(0)

        attack = attacks[i % len(attacks)]
        strength = float(rng.uniform(0.18, 0.55))
        tampered = tamper_share(share1, attack=attack, strength=strength, seed=int(rng.integers(0, 1_000_000)))
        X.append(detector.extract_features(tampered))
        y.append(1)

    return np.asarray(X), np.asarray(y)


def main():
    X, y = build_dataset()
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=7, stratify=y)
    model = RandomForestClassifier(
        n_estimators=45,
        max_depth=10,
        min_samples_leaf=2,
        random_state=7,
        class_weight="balanced",
        n_jobs=1,
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    cm = confusion_matrix(y_test, y_pred).tolist()
    report = classification_report(y_test, y_pred, target_names=["clean", "tampered"], output_dict=True)

    out_dir = ROOT / "app" / "ai_security" / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "model_name": "RandomForest VC share tamper detector",
        "feature_names": FEATURE_NAMES,
        "accuracy": float(acc),
        "confusion_matrix": cm,
        "classification_report": report,
        "training_samples": int(len(y)),
    }
    joblib.dump(payload, out_dir / "tamper_detector.joblib")
    with open(out_dir / "tamper_detector_metrics.json", "w", encoding="utf-8") as f:
        import json
        json.dump({k: v for k, v in payload.items() if k != "model"}, f, indent=2)

    print(f"Saved model to {out_dir / 'tamper_detector.joblib'}")
    print(f"Accuracy: {acc:.4f}")
    print("Confusion matrix:", cm)


if __name__ == "__main__":
    main()
