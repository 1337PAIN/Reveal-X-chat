/**
 * The "Create account" form's rules (app/static/js/signup-ui.js).
 *
 * A taken username used to get "Request submitted" while nothing was created.
 * The form now checks the name as it is typed and says whether it is free,
 * and asks for the password twice. These test what it says and when it lets
 * the request go -- not how it looks.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript } from './harness.mjs';

const window = {};
loadBrowserScript(join(STATIC_JS, 'signup-ui.js'), { window });
const ui = window.revealxSignupUI;

const good = { username: 'newname', password: 'correct-horse-1', confirm: 'correct-horse-1' };

test('a free name is said to be available', () => {
    assert.deepEqual(ui.usernameHint({ username: 'newname', available: true }),
        { text: 'newname is available', state: 'ok' });
});

test("a taken name shows the server's words", () => {
    const hint = ui.usernameHint({ username: 'alice', available: false,
        error: 'That username is already taken. Choose another.' });
    assert.equal(hint.state, 'bad');
    assert.match(hint.text, /already taken/);
});

test('nothing is said before there is an answer', () => {
    assert.deepEqual(ui.usernameHint(null), { text: '', state: 'idle' });
});

test('only a well-formed name is checked', () => {
    assert.equal(ui.worthChecking('ab'), false);
    assert.equal(ui.worthChecking('has space'), false);
    assert.equal(ui.worthChecking(' alice '), true);
});

test('a complete form can be sent', () => {
    assert.equal(ui.signupProblem(good), '');
    assert.equal(ui.signupProblem({ ...good, status: { username: 'newname', available: true } }), '');
});

test('a name known to be taken stops the request', () => {
    const status = { username: 'newname', available: false, error: 'That username is already taken. Choose another.' };
    assert.match(ui.signupProblem({ ...good, status }), /already taken/);
    // An answer about a different name does not count.
    assert.equal(ui.signupProblem({ ...good, status: { ...status, username: 'other' } }), '');
});

test('the passwords must match and be long enough', () => {
    assert.match(ui.signupProblem({ ...good, confirm: 'different-1' }), /do not match/);
    assert.match(ui.signupProblem({ ...good, password: 'short', confirm: 'short' }), /at least 8/);
    assert.match(ui.signupProblem({ username: 'samename1', password: 'samename1', confirm: 'samename1' }),
        /cannot match/);
});
