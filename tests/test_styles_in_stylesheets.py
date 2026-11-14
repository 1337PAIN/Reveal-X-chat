"""How things look is set in the stylesheets, not in markup or scripts.

Before: #srAnnouncements carried a long style attribute because the
.sr-only class it named was not defined anywhere; /lab/parity kept its rules
in a <style> block; a lab-parity result line, the "Load Older Messages"
button, the fade of a deleted message and the /lab drop-zone highlight were
all set property by property from script. Styles split that way cannot be
themed or overridden in one place, and markup styles are the reason the CSP
still needs 'unsafe-inline' for styles.

Show/hide toggles (style.display on the sign-in dialog, the offline
indicator, the /lab preview) are state rather than styling and stay as they
are.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = sorted((ROOT / 'app' / 'templates').glob('*.html'))
JS = ROOT / 'app' / 'static' / 'js'
CSS = '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT / 'app' / 'static' / 'css').glob('*.css'))


def read(path):
    return path.read_text(encoding='utf-8')


def test_templates_have_no_style_attributes_or_blocks():
    for template in TEMPLATES:
        markup = read(template)
        assert 'style="' not in markup, template.name
        assert '<style' not in markup, template.name


def test_scripts_build_no_markup_with_style_attributes():
    for script in JS.rglob('*.js'):
        assert 'style="' not in read(script), script.name


def test_sr_only_is_defined():
    rule = re.search(r'\.sr-only\s*\{([^}]*)\}', CSS)
    assert rule and 'clip' in rule.group(1) and 'position: absolute' in rule.group(1)


def test_presentational_script_styles_are_classes():
    messaging = read(JS / 'chat' / 'messaging.js')
    render = read(JS / 'chat' / 'render.js')
    lab = read(JS / 'rx_lab.js')
    assert 'node.style.' not in messaging
    assert "classList.add('message-leaving')" in messaging
    assert 'loadMore.style.' not in render
    assert 'load-older' in render
    assert 'dropZone.style.' not in lab
    assert "classList.toggle('dragging'" in lab or "classList.add('dragging')" in lab
    for selector in ('.message-leaving', '.load-older', '.dragging'):
        assert selector in CSS, selector


def test_the_parity_page_has_its_own_stylesheet():
    parity = read(ROOT / 'app' / 'templates' / 'lab_parity.html')
    assert "css/lab_parity.css" in parity
    assert (ROOT / 'app' / 'static' / 'css' / 'lab_parity.css').is_file()
