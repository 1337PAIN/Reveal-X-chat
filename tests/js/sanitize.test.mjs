/**
 * Escaping and sanitising (app/static/js/sanitize.js).
 *
 * This is where text somebody else typed becomes HTML this app builds. A
 * username, a bio, a device location -- all of it is interpolated into template
 * literals elsewhere in the front end, so a gap here is stored XSS against
 * every user who opens the conversation.
 *
 * The fallback matters as much as the happy path: DOMPurify comes from a CDN,
 * and a sanitiser that silently passes markup through when its library failed
 * to load is worse than none, because the calling code trusts it.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript } from './harness.mjs';

const SRC = join(STATIC_JS, 'sanitize.js');

/** Load with DOMPurify absent, which is the escaping fallback path. */
function loadWithoutDOMPurify() {
    const window = {};
    loadBrowserScript(SRC, { window, DOMPurify: undefined });
    return window.revealxSanitize;
}

/** Load with a stub DOMPurify, to prove it is preferred when present. */
function loadWithDOMPurify(sanitize = (s) => `[purified]${s}`) {
    const window = {};
    const calls = [];
    loadBrowserScript(SRC, {
        window,
        DOMPurify: { sanitize: (s) => { calls.push(s); return sanitize(s); } },
    });
    return { api: window.revealxSanitize, calls };
}

const { escapeHtml, validateUsername, debounce } = loadWithoutDOMPurify();

// ----------------------------------------------------------------- escaping

test('all five dangerous characters are escaped', () => {
    assert.equal(escapeHtml('&'), '&amp;');
    assert.equal(escapeHtml('<'), '&lt;');
    assert.equal(escapeHtml('>'), '&gt;');
    assert.equal(escapeHtml('"'), '&quot;');
    assert.equal(escapeHtml("'"), '&#039;');
});

test('the ampersand is escaped first, so entities are not double-decoded', () => {
    // Escaping < before & would turn "<" into "&lt;" and then that & into
    // "&amp;lt;" -- or, done the other way round, let "&lt;" survive as a live
    // "<". A single pass over a character class is what avoids both.
    assert.equal(escapeHtml('&lt;script&gt;'), '&amp;lt;script&amp;gt;');
    assert.equal(escapeHtml('&amp;'), '&amp;amp;');
});

test('script tags cannot survive', () => {
    const attack = '<script>alert(document.cookie)</script>';
    const escaped = escapeHtml(attack);
    assert.ok(!escaped.includes('<script'), 'no live tag');
    assert.equal(escaped, '&lt;script&gt;alert(document.cookie)&lt;/script&gt;');
});

test('attribute-breakout payloads are neutralised', () => {
    // These are the shapes that matter here: the value is interpolated into
    // src="..." and alt="..." in chat.js, and single quotes appear unescaped in
    // plenty of hand-written markup.
    const vectors = [
        '" onerror="alert(1)',
        "' onerror='alert(1)",
        '"><img src=x onerror=alert(1)>',
        "'><svg/onload=alert(1)>",
        'javascript:alert(1)',
        '"><iframe src="javascript:alert(1)">',
    ];
    for (const vector of vectors) {
        const escaped = escapeHtml(vector);
        assert.ok(!escaped.includes('<'), `no raw < in: ${vector}`);
        assert.ok(!escaped.includes('>'), `no raw > in: ${vector}`);
        assert.ok(!escaped.includes('"'), `no raw " in: ${vector}`);
        assert.ok(!escaped.includes("'"), `no raw ' in: ${vector}`);
    }
});

test('escaping is idempotent in the sense that matters', () => {
    // Escaping twice mangles the display, but must never re-expose markup.
    const once = escapeHtml('<b>hi</b>');
    const twice = escapeHtml(once);
    assert.ok(!twice.includes('<'), 'still no live markup');
});

test('empty and nullish values become the empty string, not "null"', () => {
    // `String(null)` is "null", which would render the word into the page.
    assert.equal(escapeHtml(null), '');
    assert.equal(escapeHtml(undefined), '');
    assert.equal(escapeHtml(''), '');
    assert.equal(escapeHtml(0), '');     // falsy, by the same guard
});

test('non-string input is coerced rather than thrown on', () => {
    assert.equal(escapeHtml(42), '42');
    assert.equal(escapeHtml(true), 'true');
    assert.equal(escapeHtml(['<a>']), '&lt;a&gt;');
});

test('safe text is left readable', () => {
    assert.equal(escapeHtml('Hello, world'), 'Hello, world');
    assert.equal(escapeHtml('شیئر محفوظ ہے'), 'شیئر محفوظ ہے');
    assert.equal(escapeHtml('emoji 🔐 fine'), 'emoji 🔐 fine');
});

// -------------------------------------------------------------- cleanHTML

test('cleanHTML uses DOMPurify when it is present', () => {
    const { api, calls } = loadWithDOMPurify();
    assert.equal(api.cleanHTML('<b>hi</b>'), '[purified]<b>hi</b>');
    assert.deepEqual(calls, ['<b>hi</b>'], 'the raw string reached DOMPurify');
});

test('cleanHTML falls back to escaping when DOMPurify is missing', () => {
    // The CDN being blocked must not mean markup passes through untouched.
    const { cleanHTML } = loadWithoutDOMPurify();
    assert.equal(cleanHTML('<script>alert(1)</script>'),
        '&lt;script&gt;alert(1)&lt;/script&gt;');
});

test('the fallback fails closed, not open', () => {
    const { cleanHTML } = loadWithoutDOMPurify();
    for (const vector of ['<img src=x onerror=alert(1)>', '<svg/onload=alert(1)>']) {
        assert.ok(!cleanHTML(vector).includes('<'), `escaped: ${vector}`);
    }
});

// --------------------------------------------------------- validateUsername

test('usernames matching the server rule are accepted', () => {
    for (const name of ['abc', 'alice', 'a_b-c.d', 'A1_2-3.4', 'x'.repeat(30)]) {
        assert.equal(validateUsername(name), true, `${name} should be valid`);
    }
});

test('usernames the server would reject are rejected here too', () => {
    for (const name of ['ab', '', 'x'.repeat(31), 'has space', 'has/slash',
                        'has<tag>', 'quote"', "apostrophe'", 'semi;colon',
                        'unicodé', '../traversal']) {
        assert.equal(validateUsername(name), false, `${name} should be invalid`);
    }
});

test('the username pattern is anchored at both ends', () => {
    // An unanchored regex would accept "bad name\ngood" via the newline, which
    // is the classic way this check gets bypassed.
    assert.equal(validateUsername('good\nbad name'), false);
    assert.equal(validateUsername('bad name\ngood'), false);
    assert.equal(validateUsername('\ngood'), false);
    assert.equal(validateUsername('good\n'), false);
});

// ---------------------------------------------------------------- debounce

test('debounce calls once, after the delay, with the last arguments', async () => {
    const seen = [];
    const debounced = debounce((value) => seen.push(value), 20);

    debounced('first');
    debounced('second');
    debounced('third');
    assert.deepEqual(seen, [], 'nothing has fired yet');

    await new Promise((resolve) => setTimeout(resolve, 60));
    assert.deepEqual(seen, ['third'], 'only the last call ran');
});

test('debounce fires again after settling', async () => {
    const seen = [];
    const debounced = debounce((value) => seen.push(value), 10);

    debounced('a');
    await new Promise((resolve) => setTimeout(resolve, 40));
    debounced('b');
    await new Promise((resolve) => setTimeout(resolve, 40));

    assert.deepEqual(seen, ['a', 'b']);
});

test('debounce preserves the calling context', async () => {
    // It is attached as an event listener, where `this` is the element.
    const holder = { name: 'input', seen: null };
    holder.handler = debounce(function () { this.seen = this.name; }, 10);
    holder.handler();
    await new Promise((resolve) => setTimeout(resolve, 40));
    assert.equal(holder.seen, 'input');
});
