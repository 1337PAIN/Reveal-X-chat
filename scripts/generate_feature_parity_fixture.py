"""Record the server's tamper-detection features as a fixture for the JS tests.

Milestone M2 claims the browser reproduces the server's detector. That was
demonstrated by opening /lab/parity and reading a table -- a real demonstration,
but one that only happens when somebody remembers to look.

This writes the server's feature vectors for a fixed set of images so
tests/js/tamper-features.test.mjs can assert the browser extractor produces the
same numbers, on every push, without a browser.

Regenerate after changing either extractor:

    python scripts/generate_feature_parity_fixture.py

CI verifies the fixture is still current with:

    python scripts/generate_feature_parity_fixture.py --check

That comparison is numerical, not byte-for-byte. The correlation features come
out differing in the last few bits between numpy builds -- 1.8e-14 relative
between Windows and Ubuntu, from summation order, not from any change in the
algorithm -- so a byte-exact check fails on every platform but the one that
generated the file. What matters is that nobody changed the extractor without
re-recording it, and a real change moves a feature by vastly more than that.
"""

import base64
import json
import math
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


# Comfortably above the ~2e-14 of cross-platform float noise, and a thousand
# times tighter than the 1e-9 the browser parity test allows. A genuine change
# to the extractor lands far outside both.
CHECK_TOLERANCE = 1e-12


def check(entries) -> int:
    """Compare freshly computed features against the committed fixture."""
    if not OUT.exists():
        print(f'{OUT.relative_to(ROOT)} does not exist; run without --check first.')
        return 1

    recorded = json.loads(OUT.read_text(encoding='utf-8'))
    names = recorded['featureNames']
    by_name = {case['name']: case for case in recorded['cases']}

    problems = []
    if names != [str(n) for n in FEATURE_NAMES]:
        problems.append(f'feature list changed: {names} -> {list(FEATURE_NAMES)}')

    for entry in entries:
        was = by_name.pop(entry['name'], None)
        if was is None:
            problems.append(f"{entry['name']}: not in the fixture")
            continue
        if was['pixels'] != entry['pixels']:
            problems.append(f"{entry['name']}: the test image itself changed")
        for name, new_value, old_value in zip(names, entry['features'], was['features']):
            scale = max(abs(old_value), 1e-12)
            delta = abs(new_value - old_value) / scale
            if not math.isfinite(new_value) or delta > CHECK_TOLERANCE:
                problems.append(
                    f"{entry['name']}/{name}: fixture {old_value!r} -> now {new_value!r} "
                    f"(relative {delta:.3e})"
                )

    for leftover in by_name:
        problems.append(f'{leftover}: in the fixture but no longer generated')

    if problems:
        print('The fixture no longer matches the server implementation:')
        for problem in problems:
            print('  -', problem)
        print()
        print('If the change was intended, re-record it:')
        print('    python scripts/generate_feature_parity_fixture.py')
        return 1

    print(f'OK: {len(entries)} cases match the fixture within {CHECK_TOLERANCE:g} relative')
    return 0


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

    if '--check' in sys.argv:
        return check(entries)

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
