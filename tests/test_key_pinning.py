"""The browser half of key pinning: the UI consults the pin before sending.

The pin logic itself (first-seen pinning, change detection, acceptance, the
safety number) is exercised against the shipped e2ee.js in
tests/js/e2ee.test.mjs. chat.js cannot be loaded there -- it needs the page --
so these check that it is wired in where it has to be.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHAT = (ROOT / 'app' / 'static' / 'js' / 'chat.js').read_text(encoding='utf-8')
INDEX = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')


def body_of(signature):
    start = CHAT.index(signature)
    end = re.search(r'\n}\n', CHAT[start:]).end()
    return CHAT[start:start + end]


@pytest.mark.parametrize('signature,encrypt_call', [
    ('async function sendMessage()', 'revealxE2EE.encrypt('),
    ('async function sendVoiceMessage(', 'revealxE2EE.encryptBytes('),
])
def test_sending_checks_the_pin_before_encrypting(signature, encrypt_call):
    body = body_of(signature)
    check = body.index("checkPeerPin(")
    assert "=== 'changed'" in body[check:check + 200]
    assert 'openSafetyChangeDialog(' in body[check:check + 300]
    assert check < body.index(encrypt_call), 'the pin is checked after encrypting'


def test_the_change_dialog_exists_and_needs_an_explicit_choice():
    for element_id in ('safetyChangeModal', 'safetyChangeNumber',
                       'safetyChangeAccept', 'safetyChangeCancel'):
        assert f'id="{element_id}"' in INDEX
    accept = CHAT[CHAT.index("safetyChangeAccept.addEventListener"):]
    accept = accept[:accept.index('});\n}')]
    assert 'acceptPeerKey(' in accept
    cancel = CHAT[CHAT.index("safetyChangeCancel.addEventListener"):]
    cancel = cancel[:cancel.index('});\n}')]
    assert 'acceptPeerKey(' not in cancel, '"Not now" must not trust the key'


def test_the_key_manager_shows_safety_numbers_not_fingerprints():
    assert 'revealxE2EE.fingerprint(' not in CHAT
    manager = body_of('async function renderKeyManager()')
    assert 'safetyNumber(' in manager
    assert 'setPeerVerified(' in manager


def test_pins_are_checked_whenever_keys_arrive():
    handler = CHAT[CHAT.index("socket.on('users_updated'"):]
    handler = handler[:handler.index('\n});')]
    assert 'refreshPeerPins()' in handler
    assert 'refreshPeerPins()' in body_of('async function initE2EEKeys(')
