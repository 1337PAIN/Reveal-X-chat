"""Nothing empty or broken is shown as if it were content.

* The sender's share bubble drew an empty .tamper-panel (only the recipient
  runs the checks), which rendered as a blank glass box.
* The reconstruction panel's three previews were <img src=""> before
  anything was loaded: a broken-image icon with alt text, under headings
  that suggested an image was there.
* /lab's "Enhanced output" card kept describing enhancement when the server
  had fallen back to the plain reconstruction (tests/js/lab-format.test.mjs).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'app' / 'static'
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
LAB = (ROOT / 'app' / 'templates' / 'lab.html').read_text(encoding='utf-8')
GLASS = (STATIC / 'css' / 'glass.css').read_text(encoding='utf-8')
STYLE = (STATIC / 'css' / 'style.css').read_text(encoding='utf-8')
RENDER = (STATIC / 'js' / 'chat' / 'render.js').read_text(encoding='utf-8')
LAB_JS = (STATIC / 'js' / 'rx_lab.js').read_text(encoding='utf-8')


def test_senders_get_no_tamper_panel_and_an_empty_one_is_hidden():
    share = RENDER[RENDER.index("} else if (msg.type === 'share') {"):]
    assert re.search(r"\$\{!isSent \? '<div class=\"tamper-panel\" data-role=\"tamper-panel\"></div>' : ''\}", share)
    assert re.search(r'\.tamper-panel:empty\s*\{[^}]*display:\s*none', GLASS)


def test_previews_are_hidden_until_they_have_an_image():
    for element_id, placeholder in (('share1Preview', 'Not loaded yet'),
                                    ('share2Preview', 'Not loaded yet'),
                                    ('reconstructedPreview', 'Not reconstructed yet')):
        tag = re.search(rf'<img id="{element_id}"[^>]*>', INDEX).group(0)
        assert 'class="preview-img"' in tag, element_id
        after = INDEX[INDEX.index(tag) + len(tag):][:200]
        assert f'<p class="preview-placeholder">{placeholder}</p>' in after, element_id
    assert re.search(r'\.preview-img\[src=""\],\s*\.preview-img:not\(\[src\]\)\s*\{[^}]*display:\s*none', STYLE)
    assert re.search(r'\.preview-img\[src\]:not\(\[src=""\]\)\s*\+\s*\.preview-placeholder\s*\{[^}]*display:\s*none', STYLE)


def test_the_enhanced_card_caption_follows_the_fallback():
    assert 'id="enhancedTitle"' in LAB and 'id="enhancedCaption"' in LAB
    assert 'revealxLabFormat.enhancedCaption(' in LAB_JS
    assert LAB.index('js/lab-format.js') < LAB.index('js/rx_lab.js')
