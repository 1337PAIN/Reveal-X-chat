"""Estimate how much the tamper detector's accuracy figure can be trusted.

    python scripts/evaluate_tamper_detector.py          # writes docs/EVALUATION.md
    python scripts/evaluate_tamper_detector.py --check  # exit 1 if the docs are stale

The README quotes 93.33%: the shipped model on one held-out split of 60
shares. One split of 60 says little about spread, so this repeats stratified
5-fold cross-validation five times over the same dataset, built by the same
code (scripts/train_tamper_detector.py, build_dataset) with the same
hyperparameters (MODEL_PARAMS), and reports mean and standard deviation.

Fresh models are fitted inside the folds only. The shipped model is never
refitted or re-exported: its sha256, and the ONNX export's, are checked before
and after, and the script fails if either changed. The one place the shipped
model itself is used is the last table, which asks how it behaves on clean
shares of sizes it was not trained on.

Everything is seeded and nothing machine-dependent (timings, dates) is
written, so the output is the same on every run and --check can compare it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import statistics
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import (accuracy_score, confusion_matrix, precision_score, recall_score,
                             roc_auc_score, roc_curve)
from sklearn.model_selection import StratifiedKFold

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs' / 'EVALUATION.md'
SVG = ROOT / 'docs' / 'evaluation-roc.svg'
SHIPPED = [ROOT / 'app' / 'ai_security' / 'models' / 'tamper_detector.joblib',
           ROOT / 'app' / 'static' / 'models' / 'tamper_detector.onnx']

N_SPLITS = 5
N_REPEATS = 5
BASE_SEED = 42
SIZE_SIDES = [64, 80, 96, 128, 256, 512]
SIZE_SAMPLES = 200


def _training_module():
    spec = importlib.util.spec_from_file_location(
        'train_tamper_detector', ROOT / 'scripts' / 'train_tamper_detector.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _size_sensitivity(train):
    """The shipped model on clean random shares of other sizes."""
    detector = train.TamperDetector()
    rng = np.random.default_rng(BASE_SEED)
    rows = []
    for side in SIZE_SIDES:
        features = [detector.extract_features(rng.integers(0, 256, (side, side), dtype=np.uint8))
                    for _ in range(SIZE_SAMPLES)]
        probs = detector.model.predict_proba(np.asarray(features))[:, 1]
        rows.append({'side': side, 'flagged': int((probs >= 0.5).sum()), 'of': SIZE_SAMPLES,
                     'mean_probability': float(probs.mean())})
    return rows


def evaluate():
    before = {p: _sha256(p) for p in SHIPPED}
    train = _training_module()
    X, y, attacks = train.build_dataset(return_attacks=True)

    folds = []
    pooled_true, pooled_score = [], []
    detected = {a: 0 for a in train.ATTACKS}
    seen = {a: 0 for a in train.ATTACKS}
    clean_flagged = clean_seen = 0
    cm_total = np.zeros((2, 2), dtype=int)

    for repeat in range(N_REPEATS):
        # One StratifiedKFold per repeat, each shuffled with its own seed:
        # five identical splits would be one evaluation counted five times.
        splitter = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=BASE_SEED + repeat)
        for fold, (train_idx, test_idx) in enumerate(splitter.split(X, y)):
            model = train.make_model()
            model.fit(X[train_idx], y[train_idx])
            score = model.predict_proba(X[test_idx])[:, 1]
            pred = (score >= 0.5).astype(int)
            truth = y[test_idx]
            folds.append({
                'repeat': repeat, 'fold': fold,
                'accuracy': float(accuracy_score(truth, pred)),
                'precision': float(precision_score(truth, pred, zero_division=0)),
                'recall': float(recall_score(truth, pred, zero_division=0)),
                'roc_auc': float(roc_auc_score(truth, score)),
            })
            cm_total += confusion_matrix(truth, pred, labels=[0, 1])
            pooled_true.extend(truth.tolist())
            pooled_score.extend(score.tolist())
            for name, p in zip(attacks[test_idx], pred):
                if name == 'clean':
                    clean_seen += 1
                    clean_flagged += int(p)
                else:
                    seen[name] += 1
                    detected[name] += int(p)

    fpr, tpr, _ = roc_curve(pooled_true, pooled_score)
    result = {
        'samples': int(len(y)),
        'per_class': int((y == 0).sum()),
        'per_attack': {a: int((attacks == a).sum()) for a in train.ATTACKS},
        'params': dict(train.MODEL_PARAMS),
        'folds': folds,
        'summary': {k: (statistics.mean(f[k] for f in folds), statistics.stdev(f[k] for f in folds))
                    for k in ('accuracy', 'precision', 'recall', 'roc_auc')},
        'recall_by_attack': {a: detected[a] / seen[a] for a in train.ATTACKS},
        'detected_by_attack': detected,
        'seen_by_attack': seen,
        'false_positive_rate': clean_flagged / clean_seen,
        'clean_flagged': clean_flagged,
        'clean_seen': clean_seen,
        'confusion_matrix': cm_total.tolist(),
        'confusion_matrix_total': int(cm_total.sum()),
        'roc': list(zip(fpr.tolist(), tpr.tolist())),
        'pooled_auc': float(roc_auc_score(pooled_true, pooled_score)),
        'sizes': _size_sensitivity(train),
    }

    after = {p: _sha256(p) for p in SHIPPED}
    if before != after:
        raise SystemExit('the evaluation changed a shipped model file; it must never do that')
    return result


def _pct(value):
    return f'{value * 100:.2f}%'


def _pm(pair):
    mean, sd = pair
    return f'{mean * 100:.2f}% ± {sd * 100:.2f}'


def _other_misses(r, missed):
    """Which other attacks were ever missed, counted rather than characterised."""
    missing = {a: r['seen_by_attack'][a] - r['detected_by_attack'][a]
               for a in r['recall_by_attack'] if a != missed}
    missing = {a: n for a, n in missing.items() if n}
    if not missing:
        return 'Every other attack was caught every time.'
    said = ', '.join(f'{a} {n} of {r["seen_by_attack"][a]} times' for a, n in sorted(missing.items()))
    return f'Of the other attacks, the classifier missed only {said}.'


def render_markdown(r):
    s = r['summary']
    recall = r['recall_by_attack']
    missed = min(recall, key=recall.get)
    (tn, fp), (fn, tp) = r['confusion_matrix']
    lines = [
        '# Tamper detector: evaluation',
        '',
        '<!-- Generated by scripts/evaluate_tamper_detector.py. Do not edit by hand:',
        '     tests/test_evaluation.py fails if this file differs from its output. -->',
        '',
        'The README quotes **93.33%**, the shipped model\'s accuracy on one held-out',
        'split of 60 shares (`app/ai_security/models/tamper_detector_metrics.json`).',
        'That remains the figure *for the shipped model*. One split of 60 says little',
        'about how far such a figure moves, so this page estimates the spread.',
        '',
        '## Method',
        '',
        f'- The dataset is the training script\'s own: `build_dataset()` in',
        f'  `scripts/train_tamper_detector.py`, {r["samples"]} feature vectors of 96 × 96',
        f'  shares, {r["per_class"]} clean and {r["per_class"]} tampered, the tampered ones',
        '  spread over seven attacks at strengths 0.18-0.55 (' + ', '.join(
            f'{a} {n}' for a, n in r['per_attack'].items()) + ').',
        f'- Stratified {N_SPLITS}-fold cross-validation (`shuffle=True`), repeated {N_REPEATS} times',
        f'  with seeds {BASE_SEED}-{BASE_SEED + N_REPEATS - 1}: {len(r["folds"])} folds, each sample',
        f'  predicted once per repeat.',
        '- A fresh `RandomForestClassifier` is fitted inside every fold with the shipped',
        '  hyperparameters (`MODEL_PARAMS`: ' + ', '.join(f'`{k}={v!r}`' for k, v in r['params'].items()) + ').',
        '- The shipped model is not refitted or re-exported. The script checks the',
        '  sha256 of `tamper_detector.joblib` and `tamper_detector.onnx` before and',
        '  after, and fails if either changed. Only the size table below uses it.',
        '- Threshold 0.5 throughout, as in the app.',
        '',
        '## Results',
        '',
        '| Measure | Mean ± standard deviation over the 25 folds |',
        '| --- | --- |',
        f'| Accuracy | {_pm(s["accuracy"])} |',
        f'| Precision (tampered) | {_pm(s["precision"])} |',
        f'| Recall (tampered) | {_pm(s["recall"])} |',
        f'| ROC-AUC | {s["roc_auc"][0]:.4f} ± {s["roc_auc"][1]:.4f} |',
        '',
        f'The shipped model\'s 93.33% lies {"inside" if abs(0.9333 - s["accuracy"][0]) <= 2 * s["accuracy"][1] else "outside"} two standard deviations of the',
        f'cross-validated mean, so it is {"a typical draw, not a lucky split" if abs(0.9333 - s["accuracy"][0]) <= 2 * s["accuracy"][1] else "not a typical draw"}.',
        '',
        '### Recall by attack',
        '',
        'Pooled over all 25 folds: of the times a share damaged by each attack was in',
        'a test fold, how often the classifier called it tampered.',
        '',
        '| Attack | Detected | Recall |',
        '| --- | --- | --- |',
    ]
    for attack in sorted(recall, key=lambda a: (-recall[a], a)):
        lines.append(f'| {attack} | {r["detected_by_attack"][attack]} of {r["seen_by_attack"][attack]} '
                     f'| {_pct(recall[attack])} |')
    lines += [
        f'| *clean shares wrongly flagged* | {r["clean_flagged"]} of {r["clean_seen"]} '
        f'| false-positive rate {_pct(r["false_positive_rate"])} |',
        '',
        f'**What it misses: `{missed}`.** Random-noise injection is detected',
        f'{_pct(recall[missed])} of the time, little better than the false-positive rate. Adding',
        'random noise to a share that is itself uniform random noise leaves every',
        'statistic the classifier measures where it was, so no threshold on these',
        'features can see it. That is why the HMAC exists: it catches every change,',
        'noise included, and the classifier is the second opinion that localises',
        'the damage and explains its verdict. ' + _other_misses(r, missed),
        '',
        '### Confusion matrix',
        '',
        f'Summed over the 25 folds ({r["confusion_matrix_total"]} predictions: each of the',
        f'{r["samples"]} samples once per repeat).',
        '',
        '| | Predicted clean | Predicted tampered |',
        '| --- | --- | --- |',
        f'| **Clean** | {tn} | {fp} |',
        f'| **Tampered** | {fn} | {tp} |',
        '',
        '### ROC curve',
        '',
        f'Out-of-fold scores pooled across all repeats; area {r["pooled_auc"]:.4f}.',
        '',
        '![ROC curve of the cross-validated tamper classifier](evaluation-roc.svg)',
        '',
        '## The shipped model on shares of other sizes',
        '',
        'The dataset above is 96 × 96 only, as was the shipped model\'s training data.',
        'Chat shares are as large as the image the sender picked, up to 1,200 px on',
        f'the long edge, so this asks the shipped model about {SIZE_SAMPLES} clean random',
        'shares at each of several sizes. Every one of them is untampered, so every',
        '"tampered" verdict is a false positive.',
        '',
        '| Share size | Clean shares flagged as tampered | Mean P(tampered) |',
        '| --- | --- | --- |',
    ]
    for row in r['sizes']:
        lines.append(f'| {row["side"]} × {row["side"]} | {row["flagged"]} of {row["of"]} '
                     f'({_pct(row["flagged"] / row["of"])}) | {row["mean_probability"]:.3f} |')
    lines += [
        '',
        '**Below about 100 px a clean share is often called tampered.** The likely',
        'cause is sample size: with fewer pixels, a random image\'s measured entropy',
        'and histogram depart further from the ideal, and the model learned what',
        '"clean" looks like at 96 × 96 only. From 128 px up, no clean share in this',
        'sample was flagged. In the chat this means a very small image',
        '(an icon, a sticker) can show *Tampering detected* while its HMAC matches',
        'exactly; the HMAC row is the one to believe. Fixing it means training on',
        'varied sizes, which is a retraining of the shipped model and was out of',
        'scope for this evaluation.',
        '',
        '## Reproducing this page',
        '',
        '```bash',
        'python scripts/evaluate_tamper_detector.py          # rewrites this page and the SVG',
        'python scripts/evaluate_tamper_detector.py --check  # exits 1 if they are stale',
        '```',
        '',
        'It takes well under two minutes, and `tests/test_evaluation.py` runs it in',
        'the normal suite, so CI fails if this page drifts from what the code produces.',
        '',
    ]
    return '\n'.join(lines)


def render_roc_svg(r):
    """The ROC curve, drawn by hand: matplotlib is not a dependency."""
    left, top, size = 56, 24, 300
    width, height = left + size + 24, top + size + 52

    def x(v):
        return left + v * size

    def yv(v):
        return top + (1 - v) * size

    points = ' '.join(f'{x(f):.1f},{yv(t):.1f}' for f, t in r['roc'])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-labelledby="roc-title">',
        f'<title id="roc-title">ROC curve of the cross-validated tamper classifier, '
        f'area {r["pooled_auc"]:.4f}</title>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<rect x="{left}" y="{top}" width="{size}" height="{size}" fill="none" stroke="#1a1a2e" stroke-width="1"/>',
    ]
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<line x1="{x(tick):.1f}" y1="{top}" x2="{x(tick):.1f}" y2="{top + size}" '
                     f'stroke="#d0d4dc" stroke-width="0.6"/>')
        parts.append(f'<line x1="{left}" y1="{yv(tick):.1f}" x2="{left + size}" y2="{yv(tick):.1f}" '
                     f'stroke="#d0d4dc" stroke-width="0.6"/>')
        parts.append(f'<text x="{x(tick):.1f}" y="{top + size + 16}" font-size="11" '
                     f'text-anchor="middle" fill="#1a1a2e" font-family="sans-serif">{tick:g}</text>')
        parts.append(f'<text x="{left - 8}" y="{yv(tick) + 4:.1f}" font-size="11" '
                     f'text-anchor="end" fill="#1a1a2e" font-family="sans-serif">{tick:g}</text>')
    parts += [
        f'<line x1="{x(0)}" y1="{yv(0)}" x2="{x(1)}" y2="{yv(1)}" stroke="#6b7280" '
        f'stroke-width="1" stroke-dasharray="5 4"/>',
        f'<polyline points="{points}" fill="none" stroke="#2d4a7c" stroke-width="2.2" '
        f'stroke-linejoin="round"/>',
        f'<text x="{left + size / 2}" y="{height - 10}" font-size="12" text-anchor="middle" '
        f'fill="#1a1a2e" font-family="sans-serif">False-positive rate</text>',
        f'<text x="14" y="{top + size / 2}" font-size="12" text-anchor="middle" fill="#1a1a2e" '
        f'font-family="sans-serif" transform="rotate(-90 14 {top + size / 2})">True-positive rate</text>',
        f'<text x="{left + size - 8}" y="{top + size - 10}" font-size="12" text-anchor="end" '
        f'fill="#2d4a7c" font-family="sans-serif">AUC {r["pooled_auc"]:.4f}</text>',
        '</svg>',
        '',
    ]
    return '\n'.join(parts)


def main(argv):
    result = evaluate()
    markdown, svg = render_markdown(result), render_roc_svg(result)
    if '--check' in argv:
        stale = [p.name for p, text in ((DOC, markdown), (SVG, svg))
                 if not p.is_file() or p.read_text(encoding='utf-8') != text]
        if stale:
            print('stale:', ', '.join(stale), '- run python scripts/evaluate_tamper_detector.py')
            return 1
        print('docs/EVALUATION.md and docs/evaluation-roc.svg match the evaluation')
        return 0
    DOC.write_text(markdown, encoding='utf-8', newline='\n')
    SVG.write_text(svg, encoding='utf-8', newline='\n')
    s = result['summary']
    print(f'accuracy {s["accuracy"][0]:.4f} ± {s["accuracy"][1]:.4f}; '
          f'ROC-AUC {s["roc_auc"][0]:.4f} ± {s["roc_auc"][1]:.4f}; wrote {DOC.name}, {SVG.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
