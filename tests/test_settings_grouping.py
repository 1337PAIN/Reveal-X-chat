"""Settings are grouped by what they do, and only real labels are labels.

"Delete Account" sat in "Data & Storage" between "Export Chat History" and
"Clear Cache", one row below a harmless button, with nothing to set the one
irreversible action apart. It now has an "Account" section of its own, last,
that says it cannot be undone. Each section is a <section> named by its
heading, so a screen reader can list and jump between them.

"Profile Photo" was a <label> tied to nothing (no for, nothing inside it).
A label that labels nothing is noise to assistive technology; it is the
group's caption now, and the photo controls are a named group.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
CSS = '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT / 'app' / 'static' / 'css').glob('*.css'))


def settings_body():
    start = INDEX.index('id="settingsModal"')
    return INDEX[start:INDEX.index('class="settings-footer"', start)]


def sections():
    return re.findall(r'<section class="settings-section[^"]*" aria-labelledby="([^"]+)">(.*?)</section>',
                      settings_body(), re.S)


def test_each_section_is_named_by_its_heading():
    found = sections()
    assert [name for name, _ in found] == [
        'settingsProfileHeading', 'settingsSecurityHeading', 'settingsPreferencesHeading',
        'settingsPrivacyHeading', 'settingsDataHeading', 'settingsAccountHeading']
    for name, body in found:
        assert f'<h3 id="{name}">' in body


def test_delete_account_stands_alone_and_says_it_is_final():
    by_name = dict(sections())
    assert 'deleteAccountBtn' not in by_name['settingsDataHeading']
    account = by_name['settingsAccountHeading']
    assert 'id="deleteAccountBtn"' in account
    assert 'cannot be undone' in account
    assert '.settings-danger' in CSS


def test_no_label_labels_nothing():
    body = settings_body()
    for label in re.finditer(r'<label(\s[^>]*)?>(.*?)</label>', body, re.S):
        attrs, inner = label.group(1) or '', label.group(2)
        assert 'for="' in attrs or '<input' in inner or '<select' in inner or '<textarea' in inner, label.group(0)[:80]
    group = re.search(r'<div class="form-group" role="group" aria-labelledby="settingsPhotoCaption">', body)
    assert group and 'id="settingsPhotoCaption"' in body
