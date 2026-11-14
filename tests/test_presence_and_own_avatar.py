"""One presence indicator per contact, and an initial on your own avatar.

Presence was shown three ways on each contact: a dot at the row's left edge
(.users-list li::before), a pip on the avatar ([data-status]::after), and the
status text. The edge dot was green for everyone not marked offline, so an
"away" or "do not disturb" contact showed a green dot beside an amber or red
pip. The pip (coloured by status) and the words stay; the edge dot is gone.

Your own avatar was an <img> with no src -- an empty circle -- until a photo
was uploaded, while every contact without one shows an initial. It shows
yours now, and keeps it in step with a username change.
"""

import re
from pathlib import Path

from chat_source import chat_source

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
CSS = '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT / 'app' / 'static' / 'css').glob('*.css'))
CHAT = chat_source()


def test_there_is_no_second_presence_dot():
    assert not re.search(r'\.users-list li(\.[a-z-]+)?::before', CSS)
    # The pip and its status colours stay.
    assert '.users-list li[data-status="away"]::after' in CSS


def test_your_avatar_has_an_initial_slot():
    picker = INDEX[INDEX.index('class="profile-picker"'):]
    picker = picker[:picker.index('</button>')]
    tag = re.search(r'<span[^>]*id="profileInitial"[^>]*>', picker).group(0)
    assert 'aria-hidden="true"' in tag
    assert picker.index('id="profilePreview"') < picker.index('id="profileInitial"')


def test_the_initial_gives_way_to_a_photo():
    assert re.search(r'\.profile-preview:not\(\[src\]\)', CSS)
    assert re.search(r'\.profile-preview\[src\]:not\(\[src=""\]\)\s*~\s*\.profile-initial', CSS)


def test_the_initial_follows_sign_in_and_renames():
    define = CHAT[CHAT.index('export function showOwnInitial('):]
    define = define[:define.index('\n}\n')]
    assert "getElementById('profileInitial')" in define
    assert '.toUpperCase()' in define
    sign_in = CHAT[CHAT.index("socket.on('auth_success'"):]
    sign_in = sign_in[:sign_in.index('\n});')]
    assert 'showOwnInitial(state.currentUser.username)' in sign_in
    saved = CHAT[CHAT.index("socket.on('settings_saved'"):]
    saved = saved[:saved.index('\n});')]
    assert 'showOwnInitial(data.username)' in saved
