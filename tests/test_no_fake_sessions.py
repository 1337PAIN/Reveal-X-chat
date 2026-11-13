"""The "Active devices" dialog is gone until it can show real sessions.

It listed one hard-coded entry ("Windows (Current Browser)") built from the
user agent, and its "Logout All Devices" button showed an alert and did
nothing else. In a security product a control that claims to end other
sessions and does not is worse than no control: a user who thinks a stolen
session was ended stops worrying about it.

The server now knows every signed-in device through the resume-token table
(models.issue_resume_token / bump_session_epoch), which is where a real one
should be built from.
"""

from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / 'app'
FILES = [p for p in list(APP.joinpath('templates').glob('*.html'))
         + list(APP.joinpath('static', 'js').rglob('*.js'))
         + list(APP.joinpath('static', 'css').glob('*.css'))
         if 'vendor' not in p.parts]


@pytest.mark.parametrize('name', ['logoutAllDevices', 'sessionListModal',
                                  'openActiveDevicesModal', 'closeActiveDevicesModal',
                                  'Active Devices'])
def test_nothing_of_the_fake_dialog_remains(name):
    found = [str(p.relative_to(APP)) for p in FILES if name in p.read_text(encoding='utf-8')]
    assert not found, f'{name} still appears in {found}'
