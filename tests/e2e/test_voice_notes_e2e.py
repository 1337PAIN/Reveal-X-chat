"""Voice notes end to end, with Chromium's fake microphone.

The other browser tests run with no audio input at all (that is how the
"No microphone was found" message is tested). These launch Chromium with a
fake capture device, which plays a tone, so a voice note can really be
recorded, encrypted, sent, decrypted, drawn and played -- and the player and
recording bar can be scanned by axe in both themes.
"""

import pytest

from e2e_helpers import ALICE, BOB, new_page, shot, sign_in
from test_accessibility import FREEZE, axe_violations
from test_flows import open_chat_by_keyboard

pytestmark = pytest.mark.e2e


# mic_browser (Chromium with a fake microphone) is in conftest.py.


def themed(page, theme):
    page.context.add_init_script(f"localStorage.setItem('revealx_theme', '{theme}')")
    return page


def record_and_send(alice, seconds=2.5):
    alice.click('#voiceBtn')
    alice.wait_for_selector('#voiceRecorder:not(.hidden)')
    assert alice.get_attribute('#voiceBtn', 'aria-label') == 'Send voice message'
    # The live meter moves: some bar above the silent level.
    alice.wait_for_function(
        "() => [...document.querySelectorAll('#voiceLevels .voice-bar')]"
        ".some((b) => !b.classList.contains('lv1'))", timeout=10000)
    alice.wait_for_timeout(int(seconds * 1000))
    return alice


@pytest.mark.parametrize('theme', ('dark', 'light'))
def test_a_voice_note_is_recorded_sent_drawn_and_played(mic_browser, server, theme):
    alice = sign_in(themed(new_page(mic_browser), theme), server, ALICE)
    bob = sign_in(themed(new_page(mic_browser), theme), server, BOB)
    try:
        open_chat_by_keyboard(alice, BOB[0])
        open_chat_by_keyboard(bob, ALICE[0])

        record_and_send(alice)
        alice.add_style_tag(content=FREEZE)
        shot(alice, f'voice-recording-{theme}')
        violations = axe_violations(alice)
        assert not violations, 'recording bar:\n' + '\n'.join(violations)
        alice.click('#voiceBtn')
        alice.wait_for_selector('#voiceRecorder.hidden', state='attached')

        note = bob.locator('#messagesContainer .voice-note').last
        note.wait_for(timeout=20000)
        # The waveform is drawn from the recording, and the length comes from
        # the decoded audio -- not "0:00" from a WebM that carries none.
        bob.wait_for_function(
            "() => { const n = [...document.querySelectorAll('#messagesContainer .voice-note')].pop();"
            " return n && n.querySelector('.voice-time').textContent !== '0:00'; }", timeout=15000)
        assert note.locator('.voice-bar').count() == 36

        play = note.locator('.voice-play')
        assert play.get_attribute('aria-label') == 'Play voice message'
        play.click()
        bob.wait_for_function(
            "() => [...document.querySelectorAll('#messagesContainer .voice-note')].pop()"
            ".querySelector('.voice-play').getAttribute('aria-label') === 'Pause voice message'")
        bob.wait_for_function(
            "() => [...document.querySelectorAll('#messagesContainer .voice-note')].pop()"
            ".querySelectorAll('.voice-bar.played').length > 0", timeout=10000)

        speed = note.locator('.voice-speed')
        speed.click()
        assert speed.inner_text() == '1.5×'

        bob.add_style_tag(content=FREEZE)
        shot(bob, f'voice-note-{theme}')
        violations = axe_violations(bob)
        assert not violations, 'voice note:\n' + '\n'.join(violations)
        assert not alice.errors and not bob.errors, alice.errors + bob.errors
    finally:
        alice.context.close()
        bob.context.close()


def test_a_discarded_recording_sends_nothing(mic_browser, server):
    alice = sign_in(new_page(mic_browser), server, ALICE)
    bob = sign_in(new_page(mic_browser), server, BOB)
    try:
        open_chat_by_keyboard(alice, BOB[0])
        open_chat_by_keyboard(bob, ALICE[0])
        record_and_send(alice, seconds=1)
        alice.click('#voiceCancelBtn')
        alice.wait_for_selector('#voiceRecorder.hidden', state='attached')
        assert alice.is_visible('#messageInput'), 'the text field is back'
        bob.wait_for_timeout(2000)
        assert bob.locator('#messagesContainer .voice-note').count() == 0
    finally:
        alice.context.close()
        bob.context.close()


def test_the_voice_note_fits_a_phone(mic_browser, server):
    alice = sign_in(new_page(mic_browser), server, ALICE)
    bob = sign_in(new_page(mic_browser, 375, 812), server, BOB)
    try:
        open_chat_by_keyboard(alice, BOB[0])
        bob.click('#sidebarToggle')
        open_chat_by_keyboard(bob, ALICE[0])
        record_and_send(alice, seconds=1.5)
        alice.click('#voiceBtn')
        note = bob.locator('#messagesContainer .voice-note').last
        note.wait_for(timeout=20000)
        box = note.bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= 375, box
        # Every bar is visible: none is clipped off the end of the waveform.
        clipped = bob.evaluate("""() => {
            const note = [...document.querySelectorAll('#messagesContainer .voice-note')].pop();
            const wave = note.querySelector('.voice-wave').getBoundingClientRect();
            return [...note.querySelectorAll('.voice-bar')]
                .filter((b) => b.getBoundingClientRect().right > wave.right + 0.5).length;
        }""")
        assert clipped == 0, f'{clipped} bars cut off'
        bob.add_style_tag(content=FREEZE)
        shot(bob, 'voice-note-phone')
        violations = axe_violations(bob)
        assert not violations, 'voice note at 375 px:\n' + '\n'.join(violations)
    finally:
        alice.context.close()
        bob.context.close()
