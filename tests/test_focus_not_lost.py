"""Focus is not dropped to <body> when what held it goes away.

Found in the keyboard-only walkthrough. After signing in, focus was in the
sign-in form that had just been hidden; after deleting a message it was on
that message's "More actions" button, removed with the message. Both times
it fell to <body>, so the next Tab started again from the top of the page.
(Dialogs opening and closing are handled generally in modal-focus.js,
tests/js/modal-focus.test.mjs.)
"""

from chat_source import chat_source

CHAT = chat_source()


def body_of(signature):
    start = CHAT.index(signature)
    return CHAT[start:CHAT.index('\n}', start)]


def test_signing_in_moves_focus_to_the_account_search():
    handler = CHAT[CHAT.index("socket.on('auth_success'"):]
    handler = handler[:handler.index('\n});')]
    assert "getElementById('userSearchInput')" in handler
    assert 'search.focus()' in handler
    # Only when focus was actually lost, so a resume does not steal it.
    assert 'authModal.contains(focused)' in handler


def test_deleting_a_message_moves_focus_to_the_composer():
    delete = body_of('async function deleteMessage(')
    emit = delete.index("socket.emit('delete_message'")
    assert delete.index('messageInput.focus()') > emit
