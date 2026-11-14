"""docs/EVALUATION.md is what scripts/evaluate_tamper_detector.py says, and
running it leaves the shipped model alone.

The README's 93.33% is one held-out split of 60 shares. The evaluation script
estimates its spread with repeated stratified cross-validation, fitting fresh
models inside the folds only. These tests rerun it and compare: a doc that
quotes numbers the script no longer produces fails here, and so does a script
that touches tamper_detector.joblib or the ONNX export.
"""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'evaluate_tamper_detector.py'
SHIPPED = [ROOT / 'app' / 'ai_security' / 'models' / 'tamper_detector.joblib',
           ROOT / 'app' / 'static' / 'models' / 'tamper_detector.onnx']


def _load():
    spec = importlib.util.spec_from_file_location('evaluate_tamper_detector', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def evaluation():
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in SHIPPED}
    module = _load()
    result = module.evaluate()
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in SHIPPED}
    assert before == after, 'the evaluation changed a shipped model file'
    return module, result


def test_the_docs_are_the_scripts_output(evaluation):
    module, result = evaluation
    assert (ROOT / 'docs' / 'EVALUATION.md').read_text(encoding='utf-8') == module.render_markdown(result), \
        'docs/EVALUATION.md is stale: run python scripts/evaluate_tamper_detector.py'
    assert (ROOT / 'docs' / 'evaluation-roc.svg').read_text(encoding='utf-8') == module.render_roc_svg(result)


def test_five_by_five_stratified_folds_with_fresh_models(evaluation):
    _, result = evaluation
    assert len(result['folds']) == 25
    # Every sample is predicted exactly once per repeat.
    assert result['confusion_matrix_total'] == 5 * result['samples']
    for fold in result['folds']:
        assert 0.0 <= fold['accuracy'] <= 1.0
        assert 0.0 <= fold['roc_auc'] <= 1.0


def test_every_attack_has_a_recall_and_noise_is_the_one_it_misses(evaluation):
    """The README says the classifier is blind to random-noise injection; the
    evaluation has to show that rather than average it away."""
    _, result = evaluation
    recall = result['recall_by_attack']
    assert set(recall) == {'block', 'noise', 'blur', 'brightness', 'crop', 'scratch', 'jpeg'}
    assert min(recall, key=recall.get) == 'noise'


def test_the_readme_keeps_the_held_out_figure_and_links_the_spread():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert '93.33%' in readme
    assert 'docs/EVALUATION.md' in readme
