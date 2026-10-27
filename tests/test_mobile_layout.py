"""The layout rules a phone depends on, and an iPhone depends on twice.

None of this fails loudly. A stylesheet with the wrong numbers in it still
loads, still parses and still renders: the controls simply end up a pixel out,
or a field gets squeezed to 34px, or Safari quietly magnifies the page because
a text field was 14px. Nothing throws, nothing logs, and nobody finds out until
the project is being demonstrated on somebody's phone.

So these are contract tests, not appearance tests. They do not say what the app
should look like; they say that the rules the measured fixes depend on are
still present, and -- in the case of the recording state -- that the half that
emits a class and the half that acts on it have not drifted apart.

The measurements behind each rule were taken at 320x720, 390x844 and 430x932,
signed in as an administrator with the install prompt showing: the state in
which the top bar carries the most controls.
"""

import re

import pytest

CSS_PATH = 'app/static/css/style.css'
CHAT_JS_PATH = 'app/static/js/chat.js'

# Every interactive control is held to this by the touch-target rule. The
# composer's text field is not a .btn, so it was never covered by it.
TOUCH_TARGET_PX = 44

# iOS Safari magnifies the whole document when a focused field is smaller than
# this, and does not undo it afterwards.
IOS_NO_ZOOM_PX = 16


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


@pytest.fixture(scope='module')
def css():
    return read(CSS_PATH)


@pytest.fixture(scope='module')
def chat_js():
    return read(CHAT_JS_PATH)


def media_blocks(css, prelude):
    """Every block body declared under a given @media prelude.

    Brace-matched rather than regex-matched, because a media block contains
    rules that contain braces of their own.
    """
    bodies = []
    for match in re.finditer(re.escape(prelude) + r'\s*\{', css):
        depth, index = 1, match.end()
        while depth and index < len(css):
            if css[index] == '{':
                depth += 1
            elif css[index] == '}':
                depth -= 1
            index += 1
        bodies.append(css[match.end():index - 1])
    return bodies


def rule_body(css, selector):
    """The declarations of every rule whose selector list starts with
    `selector`, joined. Every rule, not the first: a later rule overriding an
    earlier one is how most of these fixes are written, and a helper that
    stopped at the first match would report the value the cascade discards.
    Returns None when there is no such rule at all."""
    bodies = re.findall(
        r'(?:^|[};])\s*' + re.escape(selector) + r'\s*\{([^}]*)\}', css, re.MULTILINE
    )
    return '\n'.join(bodies) if bodies else None


def declares(body, prop, value):
    if body is None:
        return False
    pattern = re.escape(prop) + r'\s*:\s*' + re.escape(value)
    return re.search(pattern, body) is not None


# --------------------------------------------------------------------------
# The composer row
# --------------------------------------------------------------------------

def test_composer_field_matches_the_button_height(css):
    """The field was 42px against 44px buttons -- border-box, 12px of padding
    and a 14px line box -- so it sat a pixel inside them, top and bottom."""
    body = rule_body(css, '.message-input input')
    assert body is not None, '.message-input input rule is gone'
    assert declares(body, 'min-height', f'{TOUCH_TARGET_PX}px'), (
        'the composer field no longer asks for the button height; it will '
        'render 2px short and sit a pixel inside the controls beside it'
    )


def test_composer_row_centres_its_controls(css):
    """glass.css also sets this. style.css states it so the composer does not
    depend on that file having loaded."""
    body = rule_body(css, '.message-input')
    assert declares(body, 'align-items', 'center')


@pytest.mark.parametrize('selector', ['.message-input input', '.message-search'])
def test_touch_width_fields_do_not_trigger_ios_zoom(css, selector):
    """Under 16px, focusing a field zooms the page and leaves it zoomed."""
    touch = '\n'.join(media_blocks(css, '@media (max-width: 899px)'))
    assert touch, 'the touch-width media block is gone'
    assert selector in touch, f'{selector} is no longer given a 16px floor'
    assert f'font-size: {IOS_NO_ZOOM_PX}px' in touch


# --------------------------------------------------------------------------
# Recording: a class emitted in one file and acted on in another
# --------------------------------------------------------------------------

def test_recording_class_is_emitted(chat_js):
    assert "classList.toggle('is-recording'" in chat_js, (
        'nothing tells the composer it is recording any more, so the CSS '
        'below can never match'
    )


def test_recording_class_is_acted_on(css):
    assert '.message-input.is-recording' in css, (
        'chat.js still sets is-recording but no rule uses it; on a phone the '
        'clock and discard button will squeeze the text field again'
    )


def test_recording_steps_the_field_and_send_aside_on_phones(css):
    """Measured before the fix: the field was left 40px wide at 390px and 34px
    at 320px. Flexbox was not misaligned -- it had run out of width and took
    it from the only item that could give."""
    phone = '\n'.join(media_blocks(css, '@media (max-width: 560px)'))
    recording = [line for line in phone.splitlines() if 'is-recording' in line]
    assert recording, 'the phone-width recording layout is gone'
    assert '#messageInput' in phone and '#sendBtn' in phone


# --------------------------------------------------------------------------
# The top bar
# --------------------------------------------------------------------------

def test_header_buttons_are_allowed_to_wrap(css):
    """nowrap is what crushed the logo to 12px instead: the button row never
    gave way, so the only thing left to take space from was the wordmark,
    which does not shrink and so painted across the buttons."""
    assert not re.search(r'\.header-info\s*\{[^}]*flex-wrap:\s*nowrap', css), (
        'header-info is pinned to one row again; with four controls the '
        'wordmark will overlap them'
    )
    touch = '\n'.join(media_blocks(css, '@media (max-width: 899px)'))
    assert re.search(r'\.header-info\s*\{[^}]*flex-wrap:\s*wrap', touch)


def test_wordmark_truncates_rather_than_overflowing(css):
    touch = '\n'.join(media_blocks(css, '@media (max-width: 899px)'))
    body = rule_body(touch, '.logo h1')
    assert body is not None, 'the wordmark may overflow its box again'
    assert declares(body, 'overflow', 'hidden')
    assert declares(body, 'text-overflow', 'ellipsis')
    assert declares(body, 'white-space', 'nowrap')


def test_header_button_labels_stay_on_one_line(css):
    """"AI Lab" wrapped to two lines at phone width, which made that one
    button 48px tall against its 44px neighbours."""
    touch = '\n'.join(media_blocks(css, '@media (max-width: 899px)'))
    body = rule_body(touch, '.header-info > *')
    assert declares(body, 'white-space', 'nowrap'), (
        'header button labels can wrap again, which makes one button taller '
        'than the rest of the row'
    )


def test_logo_yields_space_before_the_buttons_do(css):
    """Flexbox has no priority order, so the shrink factors are the order:
    the wordmark goes first, the button row only once the logo is down to its
    hamburger and mark."""
    touch = '\n'.join(media_blocks(css, '@media (max-width: 899px)'))
    logo = rule_body(touch, '.logo')
    info = rule_body(touch, '.header-info')
    assert logo and declares(logo, 'flex-shrink', '999')
    assert info and re.search(r'flex-shrink:\s*0\.001', info), (
        'the deliberate 0.001 is gone; 0 would stop the row wrapping at all '
        'and let it overflow instead'
    )


# --------------------------------------------------------------------------
# iOS chrome the app has to clear itself
# --------------------------------------------------------------------------

def test_bottom_sheet_clears_the_home_indicator(css):
    """The modal is position: fixed, so the .container safe-area padding never
    reaches it. On a notched iPhone its buttons sit on the home indicator."""
    phone = '\n'.join(media_blocks(css, '@media (max-width: 560px)'))
    body = rule_body(phone, '.modal-content')
    assert body is not None, 'the phone-width modal rule is gone'
    assert 'env(safe-area-inset-bottom' in body
