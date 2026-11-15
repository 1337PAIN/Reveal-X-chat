"""The voice recorder reports why the microphone failed, not just "denied".

The message logic is tested in tests/js/mic-errors.test.mjs; this checks the
page actually uses it. The old toast said "Microphone access was denied." for
every failure, including a PC with no microphone, where no prompt can appear.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / 'app' / 'templates' / 'index.html').read_text(encoding='utf-8')
MESSAGING = (ROOT / 'app' / 'static' / 'js' / 'chat' / 'messaging.js').read_text(encoding='utf-8')


def test_the_page_loads_the_messages_before_the_chat_client():
    script = TEMPLATE.index("asset('js/mic-errors.js')")
    chat = TEMPLATE.index("asset('js/chat/main.js')")
    assert script < chat


def test_the_recorder_uses_them_and_the_blanket_message_is_gone():
    assert 'revealxMicErrors.micErrorMessage(error)' in MESSAGING
    assert "'Microphone access was denied.'" not in MESSAGING
