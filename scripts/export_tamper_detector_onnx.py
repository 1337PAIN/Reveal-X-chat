"""Export the trained tamper detector to ONNX for in-browser inference.

The RandomForest is trained in Python (scripts/train_tamper_detector.py) but
runs in the browser, so the share never has to leave the client to be checked.
This script converts the pickled estimator into a portable ONNX graph and then
*proves* the conversion is faithful before writing it out.

Two things are worth knowing about the conversion:

1. ONNX Runtime Web's tree kernels take float32. sklearn splits on float64, so
   thresholds are rounded on the way in and a feature sitting within a float32
   ulp of a split could in principle fall the other way. That is not a
   theoretical worry we wave away -- the verification below runs real shares
   through both engines and fails the build if the probabilities diverge.

2. Exporting pins the model. The .joblib currently emits an
   InconsistentVersionWarning because it was pickled under scikit-learn 1.8 and
   the venv now has 1.9; ONNX has no such coupling, so the browser copy is
   immune to the scikit-learn upgrade treadmill.

Run:
    python scripts/export_tamper_detector_onnx.py
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "ai_security"))

# The pickle was written by an older scikit-learn. That is exactly the coupling
# this script exists to remove, so the warning is noise here.
warnings.filterwarnings("ignore", message=".*InconsistentVersion.*")
warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

import joblib  # noqa: E402
import onnxruntime as ort  # noqa: E402
from skl2onnx import to_onnx  # noqa: E402
from skl2onnx.common.data_types import FloatTensorType  # noqa: E402

from attacks import tamper_share  # noqa: E402
from tamper_detector import FEATURE_NAMES, TamperDetector  # noqa: E402

MODEL_IN = ROOT / "app" / "ai_security" / "models" / "tamper_detector.joblib"
MODEL_OUT = ROOT / "app" / "static" / "models" / "tamper_detector.onnx"
META_OUT = ROOT / "app" / "static" / "models" / "tamper_detector.meta.json"

# A probability gap this small cannot flip a 0.5 decision boundary unless the
# sample was already sitting on the fence, and it keeps float32 rounding honest.
MAX_PROB_DELTA = 1e-4


def build_verification_set(size: int = 96, seed: int = 1337, rounds: int = 40):
    """Real shares and real attacks -- not random feature vectors.

    Verifying on synthetic feature noise would exercise regions of the tree the
    model never sees in practice and would miss disagreements that only appear
    on the distribution that actually matters.
    """
    rng = np.random.default_rng(seed)
    detector = TamperDetector(model_path="__missing__.joblib")
    attacks = ["block", "noise", "blur", "brightness", "crop", "scratch", "jpeg"]
    features = []

    for i in range(rounds):
        share = rng.integers(0, 256, size=(size, size), dtype=np.uint8)
        features.append(detector.extract_features(share))
        tampered = tamper_share(
            share,
            attack=attacks[i % len(attacks)],
            strength=float(rng.uniform(0.18, 0.55)),
            seed=int(rng.integers(0, 1_000_000)),
        )
        features.append(detector.extract_features(tampered))

    return np.asarray(features, dtype=np.float64)


def main() -> int:
    if not MODEL_IN.is_file():
        print(f"ERROR: {MODEL_IN} not found. Run scripts/train_tamper_detector.py first.")
        return 1

    payload = joblib.load(MODEL_IN)
    model = payload["model"] if isinstance(payload, dict) else payload

    print(f"Loaded {type(model).__name__}: "
          f"{model.n_estimators} trees, {model.n_features_in_} features")

    onnx_model = to_onnx(
        model,
        initial_types=[("features", FloatTensorType([None, model.n_features_in_]))],
        target_opset=15,
        # ZipMap wraps the output in a list-of-dicts, which onnxruntime-web
        # surfaces as an awkward sequence type. A plain tensor is easier to read
        # from JS and marginally faster.
        options={id(model): {"zipmap": False}},
    )

    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    MODEL_OUT.write_bytes(onnx_model.SerializeToString())

    # ---- Verify before trusting -------------------------------------------
    X = build_verification_set()
    sk_probs = model.predict_proba(X)[:, 1]

    session = ort.InferenceSession(
        MODEL_OUT.read_bytes(), providers=["CPUExecutionProvider"]
    )
    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: X.astype(np.float32)})
    onnx_probs = np.asarray(outputs[1])[:, 1]

    delta = np.abs(sk_probs - onnx_probs)
    disagreements = int(np.sum((sk_probs >= 0.5) != (onnx_probs >= 0.5)))

    print(f"\nVerification on {len(X)} real shares (clean + 7 attack types):")
    print(f"  max probability delta : {delta.max():.3e}")
    print(f"  mean probability delta: {delta.mean():.3e}")
    print(f"  verdict disagreements : {disagreements}")

    if delta.max() > MAX_PROB_DELTA or disagreements:
        print(f"\nFAILED: ONNX output diverges from scikit-learn "
              f"(tolerance {MAX_PROB_DELTA:.0e}). Model NOT accepted.")
        MODEL_OUT.unlink(missing_ok=True)
        return 1

    META_OUT.write_text(json.dumps({
        "feature_names": FEATURE_NAMES,
        "model_name": payload.get("model_name", "RandomForest VC share tamper detector"),
        "accuracy": payload.get("accuracy"),
        "training_samples": payload.get("training_samples"),
        "opset": 15,
        "input_name": input_name,
        "input_dtype": "float32",
        "verification": {
            "samples": int(len(X)),
            "max_probability_delta": float(delta.max()),
            "verdict_disagreements": disagreements,
        },
    }, indent=2), encoding="utf-8")

    size_kb = MODEL_OUT.stat().st_size / 1024
    print(f"\nPASSED. Wrote {MODEL_OUT.relative_to(ROOT)} ({size_kb:.1f} KB)")
    print(f"        Wrote {META_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
