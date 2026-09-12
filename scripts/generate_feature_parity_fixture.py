"""Record the server's tamper-detection features as a fixture for the JS tests.

Milestone M2 claims the browser reproduces the server's detector. That was
demonstrated by opening /lab/parity and reading a table -- a real demonstration,
but one that only happens when somebody remembers to look.

This writes the server's feature vectors for a fixed set of images so
tests/js/tamper-features.test.mjs can assert the browser extractor produces the
same numbers, on every push, without a browser.

Regenerate after changing either extractor:

    python scripts/generate_feature_parity_fixture.py
"""

import base64
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai_security.tamper_detector import FEATURE_NAMES, TamperDetector  # noqa: E402

OUT = ROOT / 'tests' / 'js' / 'fixtures' / 'feature-parity.json'


def cases():
    """Images chosen to exercise the edges of the feature code, not just the middle."""
    rng = np.random.default_rng(20260913)

    yield 'uniform-random-96', rng.integers(0, 256, (96, 96), dtype=np.uint8)
    yield 'uniform-random-64', rng.integers(0, 256, (64, 64), dtype=np.uint8)

    # Non-square, and not a multiple of the block size: the ragged edge blocks
    # are the part the two implementations most easily disagree about.
    yield 'ragged-70x53', rng.integers(0, 256, (53, 70), dtype=np.uint8)

    # Flat: zero variance, zero edge density. Any divide-by-count bug shows here
    # as a NaN on one side and a 0 on the other.
    yield 'flat-mid', np.full((96, 96), 128, dtype=np.uint8)
    yield 'flat-black', np.zeros((96, 96), dtype=np.uint8)
    yield 'flat-white', np.full((96, 96), 255, dtype=np.uint8)

    # Maximum edge energy, and a single hard edge.
    checker = np.indices((96, 96)).sum(axis=0) % 2
    yield 'checkerboard', (checker * 255).astype(np.uint8)

    half = np.zeros((96, 96), dtype=np.uint8)
    half[:, 48:] = 255
    yield 'half-split', half

    # A smooth ramp: low edge density, high variance, every value used once.
    ramp = np.tile(np.linspace(0, 255, 96, dtype=np.uint8), (96, 1))
    yield 'gradient', ramp

    # Two-valued, so unique_ratio lands at its floor.
    yield 'two-tone', ((rng.integers(0, 2, (96, 96))) * 200 + 20).astype(np.uint8)


def main() -> int:
    detector = TamperDetector()
    entries = []

    for name, image in cases():
        features = detector.extract_features(image)
        entries.append({
            'name': name,
            'width': int(image.shape[1]),
            'height': int(image.shape[0]),
            # Row-major (the order the browser reads canvas pixels in), base64
            # encoded -- as an integer array this file was 357 KB of digits and
            # unreadable in a diff either way.
            'pixels': base64.b64encode(image.tobytes()).decode('ascii'),
            'features': [float(v) for v in features],
        })
        if not np.all(np.isfinite(features)):
            print(f'  WARNING: {name} produced a non-finite feature')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        'generatedBy': 'scripts/generate_feature_parity_fixture.py',
        'featureNames': list(FEATURE_NAMES),
        'cases': entries,
    }), encoding='utf-8')

    total = sum(e['width'] * e['height'] for e in entries)
    print(f'wrote {OUT.relative_to(ROOT)}: {len(entries)} cases, {total:,} pixels')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
