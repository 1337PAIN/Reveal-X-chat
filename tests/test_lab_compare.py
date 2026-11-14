"""/lab compares the original and the reconstruction in one frame.

They were two cards apart, so judging what an attack cost the image meant
looking back and forth. A compare card stacks them: the original left of a
divider, the reconstruction right of it, and a labelled slider moves the
divider. The split is a custom property set through the CSSOM (no style
attribute), and the slider is an ordinary range input, so arrow keys move
it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = (ROOT / 'app' / 'templates' / 'lab.html').read_text(encoding='utf-8')
LAB_JS = (ROOT / 'app' / 'static' / 'js' / 'rx_lab.js').read_text(encoding='utf-8')
CSS = (ROOT / 'app' / 'static' / 'css' / 'rx_lab.css').read_text(encoding='utf-8')


def test_the_two_images_share_one_frame():
    frame = LAB[LAB.index('class="compare-frame"'):]
    frame = frame[:frame.index('</div>')]
    assert frame.index('id="imgCompareOriginal"') < frame.index('id="imgCompareReconstructed"')
    assert re.search(r'<img id="imgCompareReconstructed"[^>]*class="compare-top"', frame)


def test_the_slider_is_labelled():
    assert re.search(r'<label[^>]*for="compareSplit"[^>]*>', LAB)
    slider = re.search(r'<input[^>]*id="compareSplit"[^>]*>', LAB).group(0)
    assert 'type="range"' in slider and 'min="0"' in slider and 'max="100"' in slider
    assert 'id="compareSplitValue"' in LAB


def test_the_split_is_a_custom_property_clipping_the_top_image():
    assert re.search(r'\.compare-top\s*\{[^}]*clip-path:\s*inset\(0 0 0 var\(--split', CSS)
    assert "setProperty('--split'" in LAB_JS
    assert 'imgCompareOriginal' in LAB_JS and 'imgCompareReconstructed' in LAB_JS
