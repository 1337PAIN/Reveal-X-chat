/**
 * askSecret(), the masked replacement for window.prompt().
 *
 * The function text is lifted out of the shipped chat.js and executed against
 * a minimal DOM stub, so these tests exercise the file that is actually
 * served rather than a copy. chat.js as a whole cannot be imported here: it
 * calls io() at the top level and expects a live document.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const CHAT = readFileSync(join(ROOT, 'app', 'static', 'js', 'chat.js'), 'utf8');

const SOURCE = CHAT.slice(
    CHAT.indexOf('function askSecret('),
    CHAT.indexOf('\nfunction usesLocalPassword('),
);

const IDS = [
    'secretPromptModal', 'secretPromptTitle', 'secretPromptBody',
    'secretPromptLabel', 'secretPromptInput', 'secretPromptConfirmGroup',
    'secretPromptConfirmLabel', 'secretPromptConfirm', 'secretPromptError',
    'secretPromptOkBtn', 'secretPromptCancelBtn',
];

function makeElement(id) {
    const classes = new Set(id === 'secretPromptModal' ? ['modal', 'hidden'] : []);
    const listeners = new Map();
    return {
        id,
        value: '',
        textContent: '',
        placeholder: '',
        type: '',
        inputMode: '',
        autocomplete: '',
        maxLength: 0,
        className: '',
        focused: false,
        classList: {
            add: (c) => classes.add(c),
            remove: (c) => classes.delete(c),
            contains: (c) => classes.has(c),
            toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
        },
        focus() { this.focused = true; },
        addEventListener(kind, fn) {
            if (!listeners.has(kind)) listeners.set(kind, new Set());
            listeners.get(kind).add(fn);
        },
        removeEventListener(kind, fn) {
            if (listeners.has(kind)) listeners.get(kind).delete(fn);
        },
        fire(kind, event = {}) {
            const handlers = listeners.get(kind);
            if (!handlers) return 0;
            for (const fn of [...handlers]) fn({ preventDefault() {}, ...event });
            return handlers.size;
        },
        listenerCount(kind) {
            return listeners.has(kind) ? listeners.get(kind).size : 0;
        },
    };
}

function harness({ dropElement } = {}) {
    const nodes = new Map();
    for (const id of IDS) {
        if (id === dropElement) continue;
        nodes.set(id, makeElement(id));
    }
    const document = { getElementById: (id) => nodes.get(id) || null };
    // eslint-disable-next-line no-new-func
    const askSecret = new Function('document', `${SOURCE}; return askSecret;`)(document);
    return { askSecret, el: (id) => nodes.get(id) };
}

describe('askSecret', () => {
    test('resolves with the typed value when confirmed', async () => {
        const { askSecret, el } = harness();
        const pending = askSecret({ title: 'Reset password', label: 'New password' });
        el('secretPromptInput').value = 'correct-horse-battery';
        el('secretPromptOkBtn').fire('click');
        assert.equal(await pending, 'correct-horse-battery');
    });

    test('resolves null when cancelled', async () => {
        const { askSecret, el } = harness();
        const pending = askSecret({});
        el('secretPromptInput').value = 'typed-then-abandoned';
        el('secretPromptCancelBtn').fire('click');
        assert.equal(await pending, null);
    });

    test('Escape cancels and Enter confirms', async () => {
        let h = harness();
        let pending = h.askSecret({});
        h.el('secretPromptInput').value = 'x';
        h.el('secretPromptModal').fire('keydown', { key: 'Escape' });
        assert.equal(await pending, null, 'Escape should cancel');

        h = harness();
        pending = h.askSecret({});
        h.el('secretPromptInput').value = 'submitted-by-enter';
        h.el('secretPromptModal').fire('keydown', { key: 'Enter' });
        assert.equal(await pending, 'submitted-by-enter', 'Enter should confirm');
    });

    test('the secret never stays in the DOM after the dialog closes', async () => {
        const { askSecret, el } = harness();
        const pending = askSecret({ confirmLabel: 'Confirm' });
        el('secretPromptInput').value = 'a-secret';
        el('secretPromptConfirm').value = 'a-secret';
        el('secretPromptOkBtn').fire('click');
        await pending;
        assert.equal(el('secretPromptInput').value, '');
        assert.equal(el('secretPromptConfirm').value, '');
        assert.ok(el('secretPromptModal').classList.contains('hidden'));
    });

    test('a mismatched confirmation is refused without closing', async () => {
        const { askSecret, el } = harness();
        let settled = false;
        const pending = askSecret({ confirmLabel: 'Confirm passcode' });
        pending.then(() => { settled = true; });

        el('secretPromptInput').value = 'passcode-one';
        el('secretPromptConfirm').value = 'passcode-two';
        el('secretPromptOkBtn').fire('click');
        await Promise.resolve();

        assert.equal(settled, false, 'a mismatch must not resolve');
        assert.ok(!el('secretPromptModal').classList.contains('hidden'),
            'the dialog must stay open so the entry can be corrected');
        assert.match(el('secretPromptError').textContent, /do not match/i);
        assert.equal(el('secretPromptInput').value, 'passcode-one',
            'the first entry is kept; prompt() used to discard both');

        el('secretPromptConfirm').value = 'passcode-one';
        el('secretPromptOkBtn').fire('click');
        assert.equal(await pending, 'passcode-one');
    });

    test('a validation failure reports in place and keeps the dialog open', async () => {
        const { askSecret, el } = harness();
        let settled = false;
        const pending = askSecret({
            validate: (v) => (v.length < 8 ? 'The password must be at least 8 characters.' : null),
        });
        pending.then(() => { settled = true; });

        el('secretPromptInput').value = 'short';
        el('secretPromptOkBtn').fire('click');
        await Promise.resolve();

        assert.equal(settled, false);
        assert.match(el('secretPromptError').textContent, /at least 8/);

        el('secretPromptInput').value = 'long-enough-now';
        el('secretPromptOkBtn').fire('click');
        assert.equal(await pending, 'long-enough-now');
    });

    test('passwords are masked and one-time codes are not', () => {
        let h = harness();
        h.askSecret({ label: 'New password' });
        assert.equal(h.el('secretPromptInput').type, 'password');

        h = harness();
        h.askSecret({ numeric: true, label: 'Authenticator code' });
        assert.equal(h.el('secretPromptInput').type, 'text');
        assert.equal(h.el('secretPromptInput').inputMode, 'numeric');
    });

    test('the body is assigned as text, so a username cannot inject markup', () => {
        const { askSecret, el } = harness();
        const hostile = '<img src=x onerror=alert(1)>';
        askSecret({ body: `Sets a new password for "${hostile}".` });
        assert.ok(el('secretPromptBody').textContent.includes(hostile),
            'the body is set verbatim as text');
        assert.equal(el('secretPromptBody').innerHTML, undefined,
            'nothing was assigned through innerHTML');
    });

    test('it refuses rather than falling back when the markup is missing', async () => {
        const { askSecret } = harness({ dropElement: 'secretPromptModal' });
        assert.equal(await askSecret({ label: 'Password' }), null);
    });

    test('listeners are removed on close, so repeated opens do not stack', async () => {
        const { askSecret, el } = harness();
        for (let i = 0; i < 3; i += 1) {
            const pending = askSecret({});
            el('secretPromptInput').value = `round-${i}`;
            el('secretPromptOkBtn').fire('click');
            assert.equal(await pending, `round-${i}`);
        }
        assert.equal(el('secretPromptOkBtn').listenerCount('click'), 0);
        assert.equal(el('secretPromptCancelBtn').listenerCount('click'), 0);
        assert.equal(el('secretPromptModal').listenerCount('keydown'), 0);
    });
});
