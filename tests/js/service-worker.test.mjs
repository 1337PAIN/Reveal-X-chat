/**
 * Service worker request scoping (app/static/sw.js).
 *
 * This exists because of a real outage, not a hypothetical one. The fetch
 * handler used to call respondWith(fetch(request)) for every request in scope,
 * cross-origin included, which re-issued Firebase's sign-in traffic through the
 * worker and broke it -- surfacing as `auth/internal-error`, a code that points
 * nowhere. It reproduced only where a worker was registered, so every harness
 * available took the plain path and looked healthy.
 *
 * shouldHandle() is the fix, and these are the cases it has to keep getting
 * right. The auth endpoints are listed explicitly: if someone widens the rule
 * later, the sign-in flow breaks here rather than in somebody's browser.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { STATIC_ROOT } from './harness.mjs';

const source = readFileSync(join(STATIC_ROOT, 'sw.js'), 'utf8');

/**
 * Pull shouldHandle() and its allowlist out of the worker.
 *
 * The file registers install/activate/fetch listeners on `self` as soon as it
 * runs, so it is not importable as-is. Rather than stub the whole ServiceWorker
 * global scope to get at one pure function, the declarations it depends on are
 * evaluated on their own -- still from the shipped source, so the rule under
 * test is the rule that ships.
 */
function loadShouldHandle(origin = 'http://localhost:5000') {
    const allowlist = source.match(/const CACHEABLE_ORIGINS = \[[\s\S]*?\];/);
    const fn = source.match(/function shouldHandle\(request\) \{[\s\S]*?\n\}/);
    assert.ok(allowlist, 'CACHEABLE_ORIGINS not found in sw.js');
    assert.ok(fn, 'shouldHandle() not found in sw.js');

    // eslint-disable-next-line no-new-func
    return new Function('self', `${allowlist[0]}\n${fn[0]}\nreturn shouldHandle;`)({
        location: { origin },
    });
}

const shouldHandle = loadShouldHandle();
const get = (url) => shouldHandle({ url, method: 'GET' });

test('same-origin app assets are cached', () => {
    assert.equal(get('http://localhost:5000/'), true, 'the app shell');
    assert.equal(get('http://localhost:5000/lab'), true);
    assert.equal(get('http://localhost:5000/static/js/chat.js'), true);
    assert.equal(get('http://localhost:5000/static/css/style.css'), true);
    assert.equal(get('http://localhost:5000/static/Xlogo.png'), true);
});

test('the allowlisted CDNs are cached', () => {
    assert.equal(get('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css'), true);
    assert.equal(get('https://cdn.jsdelivr.net/npm/dompurify@3.0.6/purify.min.js'), true);
    assert.equal(get('https://cdn.socket.io/4.5.4/socket.io.min.js'), true);
    assert.equal(get('https://fonts.googleapis.com/css2?family=Inter'), true);
    assert.equal(get('https://fonts.gstatic.com/s/inter/v1/font.woff2'), true);
});

test('Firebase and Google sign-in traffic is never touched', () => {
    // Each of these is a request the popup flow depends on. Proxying any of them
    // through the worker is what produced auth/internal-error.
    const authEndpoints = [
        'https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp',
        'https://identitytoolkit.googleapis.com/v1/accounts:lookup',
        'https://securetoken.googleapis.com/v1/token',
        'https://accounts.google.com/o/oauth2/auth',
        'https://reveal-x-aeba7.firebaseapp.com/__/auth/iframe',
        'https://reveal-x-aeba7.firebaseapp.com/__/auth/handler',
        'https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js',
        'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth-compat.js',
        'https://apis.google.com/js/api.js',
    ];
    for (const url of authEndpoints) {
        assert.equal(get(url), false, `must not intercept ${url}`);
        assert.equal(shouldHandle({ url, method: 'POST' }), false, `must not intercept POST ${url}`);
    }
});

test('API responses are never cached', () => {
    // /api/auth/config decides whether the Google button appears at all. A copy
    // cached from before Firebase was configured would keep it hidden.
    assert.equal(get('http://localhost:5000/api/auth/config'), false);
    assert.equal(get('http://localhost:5000/api/anything/else'), false);
});

test('Socket.IO polling is left alone', () => {
    assert.equal(get('http://localhost:5000/socket.io/?EIO=4&transport=polling'), false);
    assert.equal(get('http://localhost:5000/socket.io/'), false);
});

test('only GET is handled', () => {
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']) {
        assert.equal(
            shouldHandle({ url: 'http://localhost:5000/static/js/chat.js', method }),
            false,
            `${method} must not be served from cache`
        );
    }
});

test('an unknown cross-origin host is left alone', () => {
    assert.equal(get('https://example.com/tracker.js'), false);
    assert.equal(get('https://unpkg.com/some-lib'), false);
    // Not a prefix match: a lookalike host must not inherit the allowlist.
    assert.equal(get('https://cdn.jsdelivr.net.evil.example/x.js'), false);
    assert.equal(get('https://evil-cdnjs.cloudflare.com/x.js'), false);
});

test('a malformed URL is refused rather than throwing', () => {
    // A throw inside the fetch listener fails the request outright.
    assert.doesNotThrow(() => shouldHandle({ url: 'not a url', method: 'GET' }));
    assert.equal(get('not a url'), false);
});

test('the rule follows the origin it is deployed on', () => {
    // Same logic, different host: "same-origin" must mean the deployment, not
    // a hard-coded localhost.
    const onProd = loadShouldHandle('https://reveal-x.example.edu');
    assert.equal(onProd({ url: 'https://reveal-x.example.edu/static/js/chat.js', method: 'GET' }), true);
    assert.equal(onProd({ url: 'http://localhost:5000/static/js/chat.js', method: 'GET' }), false);
});

test('the cache name is versioned', () => {
    // Shipping a change without bumping this leaves users on the old worker.
    const name = source.match(/const CACHE_NAME = '([^']+)'/);
    assert.ok(name, 'CACHE_NAME not found');
    assert.match(name[1], /^revealx-cache-v\d+/, 'cache name carries a version');
});

test('HTML is not precached', () => {
    // A cached app shell is the classic "needs a hard refresh" bug: the page
    // comes back listing the old scripts, so nothing new is ever requested.
    const list = source.match(/const ASSETS_TO_CACHE = \[([\s\S]*?)\];/);
    assert.ok(list, 'ASSETS_TO_CACHE not found');
    const entries = [...list[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
    for (const entry of entries) {
        assert.ok(
            /\.(css|js|png|json|onnx|woff2?)$/.test(entry),
            `${entry} is precached but is not a static asset`
        );
    }
});
