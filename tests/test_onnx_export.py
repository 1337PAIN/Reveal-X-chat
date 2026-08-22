"""Milestone M2: the exported ONNX model must behave exactly like the trained one.

The browser copy of the detector is only trustworthy if two things hold:

  1. The ONNX graph is a faithful conversion of the scikit-learn forest, despite
     the float64 -> float32 narrowing that onnxruntime-web's tree kernels force.
  2. Everything the browser needs to run it is actually shipped -- the model, the
     metadata, and the vendored runtime. A missing file would silently disable
     in-browser detection and fall back to the server, which is exactly the
     failure a demo would not notice.

Feature-extraction parity between JavaScript and NumPy/OpenCV cannot be checked
from pytest -- it needs a real browser -- so it lives in the /lab/parity harness
instead. See test_parity_harness_is_wired for the link between the two.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.ai_security.attacks import tamper_share
from app.ai_security.tamper_detector import FEATURE_NAMES, TamperDetector

ROOT = Path(__file__).resolve().parents[1]
ONNX_PATH = ROOT / 'app' / 'static' / 'models' / 'tamper_detector.onnx'
META_PATH = ROOT / 'app' / 'static' / 'models' / 'tamper_detector.meta.json'
VENDOR = ROOT / 'app' / 'static' / 'vendor' / 'onnxruntime'

ATTACKS = ['block', 'noise', 'blur', 'brightness', 'crop', 'scratch', 'jpeg']

ort = pytest.importorskip(
    'onnxruntime',
    reason='onnxruntime is an export-time/dev dependency; the app itself does not need it',
)


@pytest.fixture(scope='module')
def session():
    if not ONNX_PATH.is_file():
        pytest.fail(
            f'{ONNX_PATH.relative_to(ROOT)} is missing. '
            'Run: python scripts/export_tamper_detector_onnx.py'
        )
    return ort.InferenceSession(str(ONNX_PATH), providers=['CPUExecutionProvider'])


@pytest.fixture(scope='module')
def detector():
    return TamperDetector()


@pytest.fixture(scope='module')
def samples(detector):
    """One clean share plus one of every attack, with their feature vectors."""
    rng = np.random.default_rng(20260826)
    rows = []
    for i in range(14):
        share = rng.integers(0, 256, size=(96, 96), dtype=np.uint8)
        rows.append(('clean', detector.extract_features(share)))
        attack = ATTACKS[i % len(ATTACKS)]
        tampered = tamper_share(
            share, attack=attack,
            strength=float(rng.uniform(0.2, 0.55)),
            seed=int(rng.integers(0, 1_000_000)),
        )
        rows.append((attack, detector.extract_features(tampered)))
    return rows


def _onnx_probabilities(session, features):
    name = session.get_inputs()[0].name
    batch = np.asarray(features, dtype=np.float32)
    outputs = session.run(None, {name: batch})
    return np.asarray(outputs[1])[:, 1]


# --------------------------------------------------------------- shipped files

def test_onnx_model_is_shipped():
    assert ONNX_PATH.is_file(), 'exported model missing'
    # A tree ensemble this small should stay tiny; a huge file means something
    # other than the intended forest was exported.
    assert ONNX_PATH.stat().st_size < 2 * 1024 * 1024


def test_metadata_matches_the_python_feature_contract():
    meta = json.loads(META_PATH.read_text(encoding='utf-8'))
    # If these ever drift apart, the browser would feed the model its 13
    # statistics in the wrong order and still get a confident-looking answer.
    assert meta['feature_names'] == FEATURE_NAMES
    assert meta['input_dtype'] == 'float32'
    assert meta['verification']['verdict_disagreements'] == 0


def test_vendored_runtime_is_present():
    """Without these the browser silently falls back to the server."""
    required = [
        'ort.wasm.min.js',
        'ort-wasm-simd-threaded.mjs',
        'ort-wasm-simd-threaded.wasm',
    ]
    missing = [name for name in required if not (VENDOR / name).is_file()]
    assert not missing, f'vendored onnxruntime-web files missing: {missing}'
    wasm = VENDOR / 'ort-wasm-simd-threaded.wasm'
    assert wasm.read_bytes()[:4] == b'\x00asm', 'wasm binary is corrupt'


def test_browser_assets_are_actually_served(client):
    """A 404 here means the feature is dead in the browser regardless of tests."""
    for url in [
        '/static/models/tamper_detector.onnx',
        '/static/models/tamper_detector.meta.json',
        '/static/vendor/onnxruntime/ort.wasm.min.js',
        '/static/js/tamper-onnx.js',
    ]:
        assert client.get(url).status_code == 200, f'{url} is not served'


# ------------------------------------------------------------------- behaviour

def test_onnx_graph_has_the_expected_signature(session):
    inputs = session.get_inputs()
    assert len(inputs) == 1
    assert inputs[0].shape[1] == len(FEATURE_NAMES)
    assert inputs[0].type == 'tensor(float)'
    # zipmap=False was requested at export time so probabilities come back as a
    # plain tensor rather than a sequence of maps, which ORT Web reads awkwardly.
    assert 'probabilities' in [o.name for o in session.get_outputs()]


def test_onnx_matches_sklearn_on_clean_and_every_attack(session, detector, samples):
    features = [f for _, f in samples]
    sk = detector.model.predict_proba(np.asarray(features))[:, 1]
    onnx = _onnx_probabilities(session, features)

    delta = np.abs(sk - onnx)
    assert delta.max() < 1e-4, (
        f'float32 narrowing changed the model: max delta {delta.max():.2e}'
    )


def test_onnx_never_flips_a_verdict(session, detector, samples):
    """The tolerance above is about probabilities; this is about decisions."""
    features = [f for _, f in samples]
    sk = detector.model.predict_proba(np.asarray(features))[:, 1] >= 0.5
    onnx = _onnx_probabilities(session, features) >= 0.5
    assert np.array_equal(sk, onnx)


def test_onnx_reproduces_the_noise_blind_spot(session, detector):
    """The browser must inherit the model's real behaviour, flaws included.

    Overwriting random pixels of a uniform-random share with more random pixels
    changes nothing measurable, so the ML detector cannot see the `noise` attack
    -- only the HMAC catches it. If the exported model ever "fixed" this, the
    export would not be faithful; the honest fix is retraining, not conversion.
    """
    rng = np.random.default_rng(4242)
    verdicts = []
    for _ in range(5):
        share = rng.integers(0, 256, size=(96, 96), dtype=np.uint8)
        noisy = tamper_share(share, attack='noise', strength=0.4,
                             seed=int(rng.integers(0, 1_000_000)))
        prob = _onnx_probabilities(session, [detector.extract_features(noisy)])[0]
        verdicts.append(prob >= 0.5)
    assert not any(verdicts), 'noise unexpectedly detected; retrain rather than celebrate'


# ------------------------------------------------------------------ the harness

def test_parity_harness_is_wired(client):
    """JS/NumPy feature parity is proven in the browser at /lab/parity.

    pytest cannot run that comparison, so the least it can do is guarantee the
    harness still loads and its reference endpoint still answers.
    """
    page = client.get('/lab/parity')
    assert page.status_code == 200
    assert b'lab-parity.js' in page.data

    import base64
    import cv2

    share = np.random.default_rng(1).integers(0, 256, size=(32, 32), dtype=np.uint8)
    ok, buf = cv2.imencode('.png', share)
    assert ok
    data_url = 'data:image/png;base64,' + base64.b64encode(buf.tobytes()).decode()

    response = client.post('/api/lab/features', json={'image': data_url})
    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert body['feature_names'] == FEATURE_NAMES
    assert len(body['features']) == len(FEATURE_NAMES)


def test_features_endpoint_rejects_junk(client):
    assert client.post('/api/lab/features', json={'image': 'not-a-data-url'}).status_code == 400
