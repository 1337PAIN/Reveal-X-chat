"""The page behind an open dialog cannot be reached with the keyboard.

The decision (what goes inert) is tested in tests/js/modal-focus.test.mjs.
This checks the helper ships, loads before the chat modules so it is
watching before the first dialog opens, and is precached for offline use.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
SW = (ROOT / 'app' / 'static' / 'sw.js').read_text(encoding='utf-8')
HELPER = (ROOT / 'app' / 'static' / 'js' / 'modal-focus.js').read_text(encoding='utf-8')


def test_the_helper_loads_before_the_chat():
    assert INDEX.index('js/modal-focus.js') < INDEX.index('js/chat/main.js')


def test_it_is_precached():
    assert "'/static/js/modal-focus.js'" in SW


def test_it_checks_focus_as_it_moves():
    # Checked on focusin rather than by making the page inert from a
    # MutationObserver: the observer runs after a closing dialog has already
    # returned focus to its trigger, which would still be inert then.
    assert "addEventListener('focusin'" in HELPER
    assert '[aria-modal="true"]' in HELPER
    assert '.inert' not in HELPER
