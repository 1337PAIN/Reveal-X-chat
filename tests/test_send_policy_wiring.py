"""chat.js applies send-policy.js and has no plaintext fallback left.

The policy is tested in tests/js/send-policy.test.mjs. This checks the two
places that used it to matter: both send paths used to catch an encryption
error and send in plaintext anyway.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'app' / 'static'
from chat_source import chat_source  # noqa: E402

CHAT = chat_source()


def body_of(signature):
    start = CHAT.index(signature)
    end = re.search(r'\n}\n', CHAT[start:]).end()
    return CHAT[start:start + end]


@pytest.mark.parametrize('signature', ['async function sendMessage()',
                                       'async function sendVoiceMessage('])
def test_an_encryption_failure_never_falls_through_to_plaintext(signature):
    body = body_of(signature)
    assert 'sending in plaintext' not in body
    encrypt = body.index("if (mode === 'encrypt')")
    failed = body.index('afterEncrypt(sealed).action', encrypt)
    # The failure branch returns before reaching any emit.
    branch = body[failed:body.index('}', body.index('return;', failed)) + 1]
    assert 'return;' in branch and 'socket.emit' not in branch


@pytest.mark.parametrize('signature', ['async function sendMessage()',
                                       'async function sendVoiceMessage('])
def test_plaintext_is_only_sent_after_the_policy_says_so(signature):
    body = body_of(signature)
    assert body.index('decideHowToSend(') < body.index('socket.emit(')


def test_consent_is_per_conversation_and_asked_for():
    helper = body_of('async function decideHowToSend(')
    assert "decision.action === 'confirm-plaintext'" in helper
    assert 'confirm(' in helper
    assert 'plaintextConsent.add(recipientId)' in helper
    assert 'plaintextConsent.has(recipientId)' in helper


def test_the_policy_script_is_loaded_before_chat_js_and_precached():
    index = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
    assert index.index("js/send-policy.js") < index.index("js/chat/main.js")
    assert "'/static/js/send-policy.js'" in (STATIC / 'sw.js').read_text(encoding='utf-8')
