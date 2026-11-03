"""No secret is collected through a browser dialog.

window.prompt() renders what the user types in cleartext, cannot mask it, and
cannot report a validation failure without closing and discarding the entry.
Four flows used to call it: the administrator password reset, turning the
second factor off, and setting or clearing the device passcode. They now go
through askSecret(), a masked modal in the application's own markup.

These are rule tests over the shipped files rather than appearance tests: they
read what is actually served and assert the property, so the regression they
guard against is a prompt() call reappearing, not a pixel moving.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'js'
TEMPLATES = Path(__file__).resolve().parent.parent / 'app' / 'templates'

# The install prompt is the beforeinstallprompt API, not a text dialog: it
# collects nothing and cannot be given a value.
ALLOWED = {('pwa.js', 'deferredPrompt.prompt()')}


def strip_comments(source):
    """Remove // and /* */ comments so a mention in prose is not a call.

    Deliberately crude -- it also blanks the inside of string literals that
    contain '//'. That is acceptable here because the only thing read back
    out is whether a call appears, and a false positive is the safe
    direction for this particular test.
    """
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return re.sub(r'(?<![:"\'])//[^\n]*', '', source)


def js_files():
    return sorted(STATIC.glob('*.js'))


def test_no_shipped_script_calls_prompt_to_collect_a_secret():
    offenders = []
    for path in js_files():
        body = strip_comments(path.read_text(encoding='utf-8'))
        for match in re.finditer(r'(?:(\w+)\s*\.\s*)?\bprompt\s*\(', body):
            owner = match.group(1)
            call = f"{owner}.prompt()" if owner else 'prompt()'
            if (path.name, call) in ALLOWED:
                continue
            line = body[:match.start()].count('\n') + 1
            offenders.append(f'{path.name}:{line} calls {call}')
    assert not offenders, (
        'a browser dialog is collecting input again, which shows the typed '
        'value in cleartext: ' + '; '.join(offenders)
    )


def test_the_masked_prompt_helper_is_shipped():
    chat = (STATIC / 'chat.js').read_text(encoding='utf-8')
    assert 'function askSecret(' in chat, 'the replacement for prompt() is missing'


@pytest.mark.parametrize('element_id', [
    'secretPromptModal', 'secretPromptTitle', 'secretPromptBody',
    'secretPromptLabel', 'secretPromptInput', 'secretPromptConfirmGroup',
    'secretPromptConfirmLabel', 'secretPromptConfirm', 'secretPromptError',
    'secretPromptOkBtn', 'secretPromptCancelBtn',
])
def test_every_element_the_helper_reaches_for_exists(element_id):
    """askSecret() dereferences each of these without a null check, so a
    missing one is a TypeError at the moment a secret is being asked for."""
    markup = (TEMPLATES / 'index.html').read_text(encoding='utf-8')
    assert f'id="{element_id}"' in markup


@pytest.mark.parametrize('field', ['secretPromptInput', 'secretPromptConfirm'])
def test_the_secret_fields_are_masked_in_the_markup(field):
    markup = (TEMPLATES / 'index.html').read_text(encoding='utf-8')
    # Slice to the next modal rather than the next </div>: the dialog nests
    # several, so the first closing tag is the end of a form group.
    block = markup[markup.index('id="secretPromptModal"'):]
    block = block[:block.index('id="totpModal"')]
    tag = re.search(r'<input[^>]*id="%s"[^>]*>' % field, block)
    assert tag, f'{field} is not an input in the dialog'
    assert 'type="password"' in tag.group(0), (
        f'{field} must default to a masked input; askSecret() relaxes it to '
        f'text only for a one-time code'
    )


def test_the_helper_clears_both_fields_when_it_closes():
    """A dismissed dialog must not leave a secret in the DOM for the next
    script that walks it."""
    chat = (STATIC / 'chat.js').read_text(encoding='utf-8')
    helper = chat[chat.index('function askSecret('):]
    helper = helper[:helper.index('\nfunction usesLocalPassword(')]
    cleanup = helper[helper.index('function cleanup()'):]
    cleanup = cleanup[:cleanup.index('function onOk()')]
    assert "input.value = ''" in cleanup
    assert "confirmInput.value = ''" in cleanup


def test_the_helper_does_not_fall_back_to_a_cleartext_dialog():
    """If the markup is absent the helper must refuse, not call prompt()."""
    chat = (STATIC / 'chat.js').read_text(encoding='utf-8')
    helper = chat[chat.index('function askSecret('):]
    helper = helper[:helper.index('\nfunction usesLocalPassword(')]
    assert 'Promise.resolve(null)' in helper
    assert 'prompt(' not in strip_comments(helper)


def test_the_admin_body_is_not_interpolated_as_markup():
    """The reset dialog puts a username in its body. askSecret assigns it with
    textContent; if that ever becomes innerHTML the username is an injection
    point into a dialog that then asks for a password."""
    chat = (STATIC / 'chat.js').read_text(encoding='utf-8')
    helper = chat[chat.index('function askSecret('):]
    helper = helper[:helper.index('\nfunction usesLocalPassword(')]
    assert 'bodyEl.textContent' in helper
    assert 'bodyEl.innerHTML' not in helper


def test_the_service_worker_cache_was_revved():
    """Shipping new JS behind an old cache name serves the old file."""
    sw = (Path(__file__).resolve().parent.parent / 'app' / 'static' / 'sw.js')
    name = re.search(r"CACHE_NAME\s*=\s*'([^']+)'", sw.read_text(encoding='utf-8'))
    assert name, 'no CACHE_NAME in the service worker'
    version = re.search(r'-v(\d+)', name.group(1))
    assert version and int(version.group(1)) >= 22, (
        f'cache name {name.group(1)!r} predates the masked-prompt change'
    )
