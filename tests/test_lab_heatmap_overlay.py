"""/lab lays the tamper heatmap over the share it describes.

The heatmap sat in its own card, three cards away from the received Share 1
it was computed from, so matching a bright patch to the pixels under it
meant comparing two images by eye. The heatmap card now shows the received
share with the heatmap on top and a labelled slider for the heatmap's
opacity: 0% is the share alone, 100% the heatmap alone. The opacity is a
CSS custom property set through the CSSOM, so no style attribute is
written into the markup.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = (ROOT / 'app' / 'templates' / 'lab.html').read_text(encoding='utf-8')
LAB_JS = (ROOT / 'app' / 'static' / 'js' / 'rx_lab.js').read_text(encoding='utf-8')
CSS = (ROOT / 'app' / 'static' / 'css' / 'rx_lab.css').read_text(encoding='utf-8')


def overlay():
    start = LAB.index('class="heat-overlay"')
    return LAB[start:LAB.index('</div>', start)]


def test_the_heatmap_sits_on_the_share():
    stack = overlay()
    assert stack.index('id="imgHeatBase"') < stack.index('id="imgHeatmap"')
    assert re.search(r'<img id="imgHeatmap"[^>]*class="heat-top"', stack)


def test_the_slider_is_labelled_and_shows_its_value():
    assert re.search(r'<label[^>]*for="heatmapOpacity"[^>]*>Heatmap opacity', LAB)
    slider = re.search(r'<input[^>]*id="heatmapOpacity"[^>]*>', LAB).group(0)
    assert 'type="range"' in slider and 'min="0"' in slider and 'max="100"' in slider
    assert 'id="heatmapOpacityValue"' in LAB


def test_the_opacity_is_a_custom_property():
    assert re.search(r'\.heat-top\s*\{[^}]*opacity:\s*var\(--heat-opacity', CSS)
    assert "setProperty('--heat-opacity'" in LAB_JS
    assert 'data.images.share1_received' in LAB_JS and 'imgHeatBase' in LAB_JS
