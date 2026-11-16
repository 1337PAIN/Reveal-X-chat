"""Opening the accounts drawer on a phone does not pop up the keyboard.

The drawer put focus on the account search as it opened, so a keyboard user
landed somewhere useful. On a touch screen focusing a text field opens the
on-screen keyboard, which then covered half the list the user opened the
drawer to see. Signing in did the same. On a touch screen focus now goes to
the drawer itself (tabindex="-1"): it is still inside the drawer, so the
focus trap and screen readers behave the same, and nothing opens a keyboard
until the user taps the search box.
"""

from pathlib import Path

from chat_source import chat_source

CHAT = chat_source()
INDEX = (Path(__file__).resolve().parent.parent / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')


def body_of(signature):
    start = CHAT.index(signature)
    return CHAT[start:CHAT.index('\n}', start)]


def test_touch_is_detected_by_the_pointer_not_the_width():
    helper = body_of('export function typingOpensKeyboard(')
    assert "matchMedia('(pointer: coarse)')" in helper


def test_the_drawer_takes_focus_itself_on_a_touch_screen():
    opening = body_of('function openSidebar(')
    guard = opening.index('typingOpensKeyboard()')
    assert opening.index('accountsSidebar.focus(') > guard
    # The search box is still where a keyboard user lands.
    assert 'userSearchInput' in opening


def test_the_drawer_can_hold_focus():
    tag = INDEX[INDEX.index('<aside id="accountsSidebar"'):]
    tag = tag[:tag.index('>')]
    assert 'tabindex="-1"' in tag


def test_signing_in_does_not_focus_the_search_on_a_touch_screen():
    handler = CHAT[CHAT.index("socket.on('auth_success'"):]
    handler = handler[:handler.index('\n});')]
    assert 'typingOpensKeyboard()' in handler
    assert handler.index('typingOpensKeyboard()') < handler.index('search.focus()')
