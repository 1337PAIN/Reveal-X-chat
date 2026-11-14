"""/lab speaks to the person using it, not to the project's assessors.

It said "Reveal-X Final Product", asked for an image "for demo", explained
that large images are resized "for fast FYP demo", and named the button
"Run Reveal-X Analysis". The copy now says what happens to the image and
what each control does.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = (ROOT / 'app' / 'templates' / 'lab.html').read_text(encoding='utf-8')
LAB_JS = (ROOT / 'app' / 'static' / 'js' / 'rx_lab.js').read_text(encoding='utf-8')
PAGE = LAB + LAB_JS


def test_no_internal_wording():
    for phrase in ('Final Product', 'for demo', 'FYP', 'Run Reveal-X Analysis',
                   'RandomForest detector will', 'Evaluation Dashboard'):
        assert phrase not in PAGE, phrase


def test_it_says_what_happens_to_the_image():
    assert 'scaled to 512 px on its longest side' in LAB
    assert 'not saved' in LAB


def test_the_button_says_what_it_does():
    assert '>Run analysis</button>' in LAB
    assert "'Run analysis'" in LAB_JS


def test_the_drop_zone_icon_is_decoration():
    icon = re.search(r'<[a-z]+[^>]*class="drop-icon"[^>]*>', LAB).group(0)
    assert 'aria-hidden="true"' in icon
