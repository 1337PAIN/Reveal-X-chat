"""Time a full tamper-detection check at the sizes the app produces.

A "full check" is what /api/share/<id>/analysis and /lab run per share:
feature extraction, the model, the suspicious-region heatmap and its PNG
encoding. Prints the median of several runs per size, in milliseconds.

    python scripts/bench_detection.py            # 256, 500 and 1200 px
    python scripts/bench_detection.py 1200 2000  # chosen sizes
"""

import os
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai_security.tamper_detector import TamperDetector  # noqa: E402

RUNS = 25


def main(sizes):
    detector = TamperDetector()
    rng = np.random.default_rng(2026)
    print(f'model: {detector.model_name}')
    for side in sizes:
        share = rng.integers(0, 256, (side, side), dtype=np.uint8)
        detector.predict(share)  # warm-up
        samples = []
        for _ in range(RUNS):
            started = time.perf_counter()
            detector.predict(share)
            samples.append((time.perf_counter() - started) * 1000)
        print(f'{side:>5} px  median {statistics.median(samples):7.1f} ms'
              f'   min {min(samples):7.1f} ms')


if __name__ == '__main__':
    main([int(a) for a in sys.argv[1:]] or [256, 500, 1200])
