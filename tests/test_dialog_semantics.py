"""Dialogs say they are dialogs, and every close button says what it closes.

The settings panel was a plain <div>: a screen reader user opening it heard
nothing about entering a dialog, and its "x" was announced as "times" or
"multiplication sign". Three more close buttons were the same bare "x" or a
generic "Close" that does not say which of several stacked panels it closes.
The sidebar's "Accounts" heading was an h3 with no h2 above it.
"""

import re
from pathlib import Path

import pytest

INDEX = (Path(__file__).resolve().parent.parent / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')


def opening_tag(element_id):
    match = re.search(rf'<[a-z]+[^>]*\bid="{element_id}"[^>]*>', INDEX)
    assert match, element_id
    return match.group(0)


def test_the_settings_panel_is_a_labelled_modal_dialog():
    tag = opening_tag('settingsModal')
    assert 'role="dialog"' in tag and 'aria-modal="true"' in tag
    assert 'aria-labelledby="settingsTitle"' in tag
    assert '<h2 id="settingsTitle">Settings</h2>' in INDEX


@pytest.mark.parametrize('element_id', re.findall(
    r'<button[^>]*\bid="([^"]+)"[^>]*class="(?:btn-close|close-btn)"', INDEX)
    + re.findall(r'<button[^>]*class="(?:btn-close|close-btn)"[^>]*\bid="([^"]+)"', INDEX))
def test_every_close_button_names_what_it_closes(element_id):
    label = re.search(r'aria-label="([^"]+)"', opening_tag(element_id))
    assert label, f'{element_id} has no aria-label'
    assert re.fullmatch(r'Close \S.*', label.group(1)), (
        f'{element_id}: "{label.group(1)}" does not say what it closes')


def test_the_sidebar_heading_is_an_h2():
    header = INDEX[INDEX.index('<div class="sidebar-header">'):]
    header = header[:header.index('</div>')]
    assert '<h2>Contacts</h2>' in header
