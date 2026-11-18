"""The chat, driven end to end in two real browsers.

Every other test of the client runs one module in Node. These run the whole
app: sign-in, key agreement between two separate IndexedDB stores, the
browser-side image split, the server's integrity check and the resume path
after a dropped connection. Each test signs in two people in two browser
contexts, which is the only way to see what one of them sees of the other.
"""

import struct
import zlib

import pytest

from e2e_helpers import ALICE, BOB, new_page, shot, sign_in

pytestmark = pytest.mark.e2e


def open_chat_by_keyboard(page, peer_name):
    """Reach the contact with Tab from the search box and open it with Enter."""
    page.focus('#userSearchInput')
    for _ in range(25):
        page.keyboard.press('Tab')
        focused = page.evaluate(
            "() => (document.activeElement.querySelector('.user-name') || {}).textContent || ''")
        if focused == peer_name:
            page.keyboard.press('Enter')
            page.wait_for_function(
                "name => document.getElementById('chatTitle').textContent.includes(name)", arg=peer_name)
            return
    raise AssertionError(f'Tab never reached the contact {peer_name}')


def send_text(page, text):
    page.fill('#messageInput', text)
    page.keyboard.press('Enter')


def message_with(page, text):
    return page.locator('#messagesContainer .message', has_text=text).last


def png_bytes(width=320, height=240):
    """A grayscale PNG with structure, built without any image library.

    Photo-sized on purpose. The tamper model was trained on 96 x 96 shares,
    and below about 100 px a genuinely clean random share is often scored
    "tampered" (64 x 64: every time; 96 x 72: 44%). That is a property of the
    shipped model, recorded in docs/EVALUATION.md, not something a browser
    test should depend on either way.
    """
    rows = b''.join(
        b'\x00' + bytes((x * 3 + y * 5 + (60 if 20 < x < 50 and 20 < y < 50 else 0)) % 256
                        for x in range(width))
        for y in range(height))

    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def send_image(page, tmp_path, name='picture.png', size=(320, 240)):
    path = tmp_path / name
    path.write_bytes(png_bytes(*size))
    page.set_input_files('#imageInput', str(path))
    page.wait_for_selector('#imagePreviewModal:not(.hidden)')
    page.click('#confirmImageBtn')


def test_sign_in_and_open_a_chat_by_keyboard(pair):
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    assert alice.evaluate("() => document.activeElement.id") != ''
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def test_text_both_ways_is_end_to_end_encrypted(pair):
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    open_chat_by_keyboard(bob, ALICE[0])

    send_text(alice, 'hello from alice')
    for page in (alice, bob):
        message = message_with(page, 'hello from alice')
        message.wait_for()
        assert 'E2EE' in message.locator('.msg-chip').inner_text()

    send_text(bob, 'and back from bob')
    for page in (alice, bob):
        message = message_with(page, 'and back from bob')
        message.wait_for()
        assert 'E2EE' in message.locator('.msg-chip').inner_text()
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def test_an_image_is_split_checked_and_reconstructed(pair, tmp_path):
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    open_chat_by_keyboard(bob, ALICE[0])
    send_image(alice, tmp_path)

    share = bob.locator('#messagesContainer .message:has([data-role="tamper-panel"])').last
    share.wait_for()
    panel = share.locator('[data-role="tamper-panel"]')
    panel.locator('text=verified intact').wait_for(timeout=30000)
    shot(bob, 'image-integrity-passed')

    share.locator('[data-action="reconstruct"]').click()
    bob.wait_for_selector('#reconstructionPanel.active')
    bob.wait_for_function("() => !document.getElementById('reconstructBtn').disabled", timeout=30000)
    bob.click('#reconstructBtn')
    bob.wait_for_function(
        "() => (document.getElementById('reconstructedPreview').src || '').startsWith('data:image')",
        timeout=30000)
    shot(bob, 'image-reconstructed')
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def test_a_rebuilt_share_is_spent_and_keeps_its_verdict(pair, tmp_path):
    """Opening a share uses up Share 1. Afterwards the bubble must not offer
    Reconstruct or Re-check again (the server would only answer "already
    opened"), must keep the verdict it showed, and frames a portrait picture
    without bands down its sides."""
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    open_chat_by_keyboard(bob, ALICE[0])
    send_image(alice, tmp_path, size=(180, 320))

    share = bob.locator('#messagesContainer .message:has([data-role="tamper-panel"])').last
    share.wait_for()
    panel = share.locator('[data-role="tamper-panel"]')
    panel.locator('text=verified intact').wait_for(timeout=30000)
    share.locator('[data-action="reconstruct"]').click()
    bob.wait_for_function("() => !document.getElementById('reconstructBtn').disabled", timeout=30000)
    bob.click('#reconstructBtn')
    share.locator('.share-card.revealed').wait_for(timeout=30000)

    assert share.locator('[data-action="reconstruct"]').is_disabled()
    assert share.locator('[data-action="recheck"]').is_disabled()
    assert 'verified intact' in panel.inner_text()
    assert 'already opened' not in panel.inner_text()

    fit = share.evaluate('''el => {
        const tile = el.querySelector('.share-tile').getBoundingClientRect();
        const img = el.querySelector('.share-image').getBoundingClientRect();
        return {tile: tile.width, img: img.width};
    }''')
    assert fit['tile'] - fit['img'] <= 3, fit
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def menu_action(page, bubble, action):
    """Open a message's "more" menu and pick an action, by its data-menu name."""
    bubble.locator('.btn-message-menu').click()
    bubble.locator(f'[data-menu="{action}"]').click()


def test_a_reply_quotes_the_message_it_answers(pair):
    """The quote is drawn from each side's own decrypted copy: the server only
    ever holds "Encrypted message" for an E2EE original."""
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    open_chat_by_keyboard(bob, ALICE[0])
    send_text(bob, 'lunch at one?')
    original = message_with(alice, 'lunch at one?')
    original.wait_for()

    menu_action(alice, original, 'reply')
    alice.wait_for_selector('#replyBar:not(.hidden)')
    assert 'lunch at one?' in alice.locator('#replyBar').inner_text()
    send_text(alice, 'yes, see you there')
    alice.wait_for_selector('#replyBar.hidden', state='attached')

    for page in (alice, bob):
        reply = message_with(page, 'yes, see you there')
        reply.wait_for()
        quote = reply.locator('.message-reply')
        quote.locator('text=lunch at one?').wait_for(timeout=15000)
        assert 'E2EE' in reply.locator('.msg-chip').inner_text()
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def test_a_reply_can_be_cancelled(pair):
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    open_chat_by_keyboard(bob, ALICE[0])
    send_text(bob, 'first')
    original = message_with(alice, 'first')
    original.wait_for()
    menu_action(alice, original, 'reply')
    alice.wait_for_selector('#replyBar:not(.hidden)')
    alice.click('#cancelReplyBtn')
    alice.wait_for_selector('#replyBar.hidden', state='attached')
    send_text(alice, 'not a reply')
    sent = message_with(bob, 'not a reply')
    sent.wait_for()
    assert sent.locator('.message-reply').count() == 0


def test_copy_puts_the_decrypted_text_on_the_clipboard(pair):
    alice, bob = pair
    alice.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    open_chat_by_keyboard(alice, BOB[0])
    open_chat_by_keyboard(bob, ALICE[0])
    send_text(bob, 'the code is 4471')
    message = message_with(alice, 'the code is 4471')
    message.wait_for()
    menu_action(alice, message, 'copy')
    alice.wait_for_function(
        "async () => (await navigator.clipboard.readText()) === 'the code is 4471'")
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def test_a_revoked_share_can_no_longer_be_opened(pair, tmp_path):
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    open_chat_by_keyboard(bob, ALICE[0])
    send_image(alice, tmp_path, 'revoke-me.png')

    mine = alice.locator('#messagesContainer .message:has([data-action="revoke"])').last
    mine.wait_for()
    theirs = bob.locator('#messagesContainer .message:has([data-role="tamper-panel"])').last
    theirs.wait_for()

    mine.locator('[data-action="revoke"]').click()
    alice.click('#confirmDialogOk')

    # The recipient can no longer reconstruct: the share is gone server-side.
    bob.wait_for_function(
        """() => {
            const shares = document.querySelectorAll('#messagesContainer .message [data-role="tamper-panel"]');
            if (!shares.length) return true;  // the message was removed outright
            const last = shares[shares.length - 1].closest('.message');
            const button = last.querySelector('[data-action="reconstruct"]');
            return !button || button.disabled;
        }""", timeout=15000)
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def test_a_dropped_connection_shows_the_banner_then_resumes(browser, server):
    """The server goes away and comes back with the same database: the page
    says it is reconnecting, then resumes its session without a sign-in."""
    alice = sign_in(new_page(browser), server, ALICE)
    try:
        server.stop()
        alice.wait_for_selector('#connectionBanner:not([hidden])', timeout=20000)
        assert 'Reconnecting' in alice.inner_text('#connectionBanner')
        shot(alice, 'connection-banner')

        server.start()
        alice.wait_for_selector('#connectionBanner', state='hidden', timeout=60000)
        # Resumed, not signed out: no sign-in form, same account.
        assert not alice.is_visible('#authModal')
        assert alice.text_content('#accountName').strip() == ALICE[0]
        open_chat_by_keyboard(alice, BOB[0])
    finally:
        if server.proc.poll() is not None:
            server.start()
        alice.context.close()


def test_a_changed_peer_key_blocks_sending_until_accepted(pair):
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    send_text(alice, 'before the key changes')
    message_with(alice, 'before the key changes').wait_for()

    # Bob makes a new key pair. To Alice that is indistinguishable from the
    # server substituting one.
    bob.get_by_role('button', name='Keys').click()
    bob.click('#regenerateKeyBtn')
    bob.click('#confirmDialogOk')
    bob.wait_for_selector('text=New key pair generated and published.')

    # The warning arrives unprompted, with the conversation open.
    alice.wait_for_selector('#safetyChangeModal:not(.hidden)')
    assert 'different from the one this device saw before' in alice.inner_text('#safetyChangeText')
    shot(alice, 'safety-number-changed')

    # "Not now" leaves sending blocked: trying again brings the warning back
    # and nothing leaves the browser.
    alice.click('#safetyChangeCancel')
    alice.wait_for_selector('#safetyChangeModal.hidden', state='attached')
    alice.fill('#messageInput', 'after the key changes')
    alice.wait_for_timeout(2500)  # past the two-second send pause
    alice.click('#sendBtn')
    alice.wait_for_selector('#safetyChangeModal:not(.hidden)')
    assert alice.locator('#messagesContainer .message', has_text='after the key changes').count() == 0
    assert bob.locator('#messagesContainer .message', has_text='after the key changes').count() == 0

    # Accepting pins the new key; the next send is encrypted to it and Bob,
    # holding only his new private key, can read it.
    alice.click('#safetyChangeAccept')
    alice.wait_for_selector('#safetyChangeModal.hidden', state='attached')
    alice.click('#sendBtn')
    bob.click('#closeKeyManagerBtn')
    open_chat_by_keyboard(bob, ALICE[0])
    message_with(bob, 'after the key changes').wait_for()
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def test_after_a_reload_only_the_recipient_can_read_a_sent_message(pair, server):
    """E2EE v4 forward secrecy, as the people see it. The sender's
    per-message key is gone once the message is sealed, so after a reload her
    own browser cannot open it -- it says so -- while the recipient still can."""
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    send_text(alice, 'readable by bob only, after a reload')
    message_with(alice, 'readable by bob only, after a reload').wait_for()

    # A reload signs the page out (the resume token lives in page memory), so
    # sign back in: same browser, same IndexedDB, same long-term key.
    sign_in(alice, server, ALICE)
    open_chat_by_keyboard(alice, BOB[0])
    alice.locator('#messagesContainer .message', has_text='Sent with forward secrecy').first.wait_for()
    assert alice.locator('#messagesContainer .message',
                         has_text='readable by bob only, after a reload').count() == 0
    shot(alice, 'forward-secrecy-own-message')

    open_chat_by_keyboard(bob, ALICE[0])
    message_with(bob, 'readable by bob only, after a reload').wait_for()
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def test_a_pc_without_a_microphone_is_told_so_not_denied(pair):
    """The server PC this project is demonstrated from has no microphone. The
    browser then refuses at once, with no prompt, and the app used to call
    that "access denied" -- leaving nothing the user could allow."""
    alice, _bob = pair
    # As if the user would allow it: the failure left is the missing device.
    alice.context.grant_permissions(['microphone'])
    has_input = alice.evaluate("""async () => (await navigator.mediaDevices.enumerateDevices())
        .some((d) => d.kind === 'audioinput')""")
    if has_input:
        pytest.skip('this browser has an audio input; the no-microphone case cannot be shown')
    open_chat_by_keyboard(alice, BOB[0])
    alice.click('#voiceBtn')
    toast = alice.locator('text=No microphone was found')
    toast.wait_for()
    assert alice.locator('text=Microphone access was denied').count() == 0
    shot(alice, 'no-microphone')


def test_opening_the_drawer_on_a_phone_does_not_open_the_keyboard(browser, server):
    """On a touch screen focus goes to the drawer, not the search box, so the
    on-screen keyboard does not cover the list."""
    context = browser.new_context(service_workers='block', viewport={'width': 390, 'height': 844},
                                  is_mobile=True, has_touch=True)
    page = context.new_page()
    try:
        sign_in(page, server, ALICE)
        assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
        assert page.evaluate("() => document.activeElement.tagName") != 'INPUT'
        page.tap('#sidebarToggle')
        page.wait_for_selector('#accountsSidebar.open')
        assert page.evaluate("() => document.activeElement.id") == 'accountsSidebar'
    finally:
        context.close()


def test_removing_a_contact_closes_the_chat_on_both_sides(pair):
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])
    open_chat_by_keyboard(bob, ALICE[0])
    choose(alice, 'Remove contact')
    alice.wait_for_selector('#confirmDialog:not(.hidden)')
    assert BOB[0] in alice.text_content('#confirmDialogTitle')
    alice.click('#confirmDialogOk')
    for page, peer in ((alice, BOB[0]), (bob, ALICE[0])):
        page.wait_for_function(
            "() => document.getElementById('chatTitle').textContent === 'Select a contact'")
        assert page.locator('#usersList button', has_text=peer).count() == 0
        assert page.is_disabled('#messageInput')
    assert not alice.errors and not bob.errors, alice.errors + bob.errors


def test_create_account_says_when_a_username_is_taken(browser, server):
    """Sign-up checks the name as it is typed; a taken one (any capitals) is
    named as taken and the request is not sent; a free one goes through."""
    page = new_page(browser)
    try:
        page.goto(server.url + '/')
        # One eye per password box you can see: in Sign in the confirm box is
        # hidden, and its eye went with it (it used to float over Login).
        reveals = page.locator('#authModal .password-reveal:visible')
        assert reveals.count() == 1
        page.click('#authModeSignUp')
        assert page.is_visible('#confirmPasswordInput') and page.is_hidden('#loginBtn')
        assert reveals.count() == 2
        page.fill('#usernameInput', ALICE[0].upper())
        page.locator('#usernameStatus', has_text='already taken').wait_for()
        page.fill('#passwordInput', 'brand-new-pass-1')
        page.fill('#confirmPasswordInput', 'brand-new-pass-1')
        page.click('#registerBtn')
        assert 'already taken' in page.text_content('#authError')

        page.fill('#usernameInput', 'e2efreshname')
        page.locator('#usernameStatus', has_text='e2efreshname is available').wait_for()
        page.fill('#confirmPasswordInput', 'something-else-1')
        page.click('#registerBtn')
        assert 'do not match' in page.text_content('#authError')
        page.fill('#confirmPasswordInput', 'brand-new-pass-1')
        page.click('#registerBtn')
        page.locator('#authNotice', has_text='Request submitted').wait_for()
        assert not page.errors, page.errors
    finally:
        page.context.close()


def choose(page, label):
    """Open the chat menu beside the contact's name and pick an item."""
    page.click('#chatMenuBtn')
    page.locator('#chatMenu [role="menuitem"]', has_text=label).click()


def row_names(page, selector='#usersList'):
    return page.eval_on_selector_all(f'{selector} .user-name', 'els => els.map(e => e.textContent)')


def test_escape_closes_contact_info_and_returns_focus(pair):
    """Every other dialog closed on Escape; Contact info did not, so a keyboard
    user had to find its close button to get back to the conversation."""
    alice, _ = pair
    open_chat_by_keyboard(alice, BOB[0])
    alice.click('#chatMenuBtn')
    alice.locator('#chatMenu [data-action="info"]').click()
    alice.wait_for_selector('#contactInfoModal:not(.hidden)')
    alice.keyboard.press('Escape')
    alice.wait_for_selector('#contactInfoModal.hidden', state='attached')
    assert alice.evaluate('() => document.activeElement.id') == 'chatMenuBtn'


def test_the_chat_menu_pins_archives_locks_and_blocks(pair):
    alice, bob = pair
    open_chat_by_keyboard(alice, BOB[0])

    choose(alice, 'Pin chat')
    alice.locator('#usersList .row-flags').first.wait_for()
    alice.click('#chatMenuBtn')
    assert alice.locator('#chatMenu [role="menuitem"]', has_text='Unpin chat').count() == 1
    alice.keyboard.press('Escape')

    choose(alice, 'Archive chat')
    alice.wait_for_selector('#archivedBtn:not([hidden])')
    assert BOB[0] not in row_names(alice)
    alice.click('#archivedBtn')
    assert row_names(alice, '#archivedList') == [BOB[0]]
    choose(alice, 'Unarchive chat')
    alice.wait_for_function("name => [...document.querySelectorAll('#usersList .user-name')]"
                            ".some(e => e.textContent === name)", arg=BOB[0])

    # Lock: the first time sets the PIN; the chat stays open in this session.
    choose(alice, 'Lock chat')
    alice.wait_for_selector('#secretPromptModal:not(.hidden)')
    alice.fill('#secretPromptInput', '2468')
    alice.fill('#secretPromptConfirm', '2468')
    alice.click('#secretPromptOkBtn')
    alice.wait_for_selector('#hideLockedBtn:not([hidden])')
    alice.click('#hideLockedBtn')
    alice.wait_for_selector('#lockedChatsBtn:not([hidden])')
    assert BOB[0] not in row_names(alice)
    assert alice.text_content('#chatTitle') == 'Select a contact'
    alice.click('#lockedChatsBtn')
    alice.fill('#secretPromptInput', '0000')
    alice.click('#secretPromptOkBtn')
    alice.locator('.app-toast', has_text='not right').wait_for()
    alice.click('#lockedChatsBtn')
    alice.fill('#secretPromptInput', '2468')
    alice.click('#secretPromptOkBtn')
    alice.wait_for_function("name => [...document.querySelectorAll('#usersList .user-name')]"
                            ".some(e => e.textContent === name)", arg=BOB[0])

    open_chat_by_keyboard(alice, BOB[0])
    choose(alice, 'Block')
    alice.click('#confirmDialogOk')
    alice.wait_for_function("() => document.getElementById('chatTitle').textContent === 'Select a contact'")
    assert BOB[0] not in row_names(alice)
    alice.locator('#blockedList', has_text=BOB[0]).wait_for(state='attached')
    assert not alice.errors and not bob.errors, alice.errors + bob.errors
