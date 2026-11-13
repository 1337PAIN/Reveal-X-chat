/**
 * The chat client as ES modules (app/static/js/chat/).
 *
 * chat.js was one 3,800-line classic script. Split into modules, three things
 * have to stay true or the app breaks in ways that only show in a browser:
 *
 *   1. Every import names something its module exports -- otherwise the whole
 *      graph fails to link and the page does nothing.
 *   2. Imports only point to modules loaded earlier. An import of a later
 *      module makes it evaluate first, which reorders start-up code that was
 *      written to run top to bottom.
 *   3. Every call that goes the other way, through late.<name>, has a module
 *      that registers <name>.
 *
 * These are checked on the shipped files, and late.js -- the one module with
 * no page dependency -- is loaded through the harness and exercised.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { STATIC_JS, importBrowserModule } from './harness.mjs';

const DIR = join(STATIC_JS, 'chat');
const read = (name) => readFileSync(join(DIR, name), 'utf8');
const ORDER = [...read('main.js').matchAll(/import '\.\/(\w+)\.js';/g)].map((m) => m[1]);
const FILES = readdirSync(DIR).filter((f) => f.endsWith('.js')).map((f) => f.replace(/\.js$/, ''));
const HELPERS = ['late', 'shared'];

function importsOf(name) {
    return [...read(`${name}.js`).matchAll(/import \{([^}]*)\} from '\.\/(\w+)\.js';/g)]
        .map((m) => ({ from: m[2], names: m[1].split(',').map((s) => s.trim()).filter(Boolean) }));
}

function exportsOf(name) {
    return new Set([...read(`${name}.js`).matchAll(/^export (?:async )?(?:function|const|let|class) (\w+)/gm)]
        .map((m) => m[1]));
}

test('main.js loads every chat module exactly once', () => {
    const expected = FILES.filter((f) => f !== 'main' && !HELPERS.includes(f)).sort();
    assert.deepEqual([...ORDER].sort(), expected);
    assert.equal(new Set(ORDER).size, ORDER.length);
});

test('every import names an export of the module it comes from', () => {
    for (const name of [...ORDER, 'main']) {
        for (const { from, names } of importsOf(name)) {
            const exported = exportsOf(from);
            for (const n of names) assert.ok(exported.has(n), `${name}.js imports ${n} from ${from}.js, which does not export it`);
        }
    }
});

test('imports only point to modules loaded earlier', () => {
    for (const [index, name] of ORDER.entries()) {
        for (const { from } of importsOf(name)) {
            if (HELPERS.includes(from)) continue;
            assert.ok(ORDER.indexOf(from) < index, `${name}.js imports from ${from}.js, which loads after it`);
        }
    }
});

test('every late-bound call is registered by some module', () => {
    const registered = new Set();
    const used = new Set();
    for (const name of ORDER) {
        const source = read(`${name}.js`);
        for (const m of source.matchAll(/registerLate\(\{([^}]*)\}\)/g)) {
            m[1].split(',').map((s) => s.trim()).filter(Boolean).forEach((n) => registered.add(n));
        }
        for (const m of source.matchAll(/\blate\.(\w+)/g)) if (m[1] !== 'js') used.add(m[1]);
    }
    assert.ok(used.size > 0);
    for (const n of used) assert.ok(registered.has(n), `late.${n} is called but never registered`);
});

test('the harness can load a chat module, and late binding works', async () => {
    const { late, registerLate } = await importBrowserModule(join(DIR, 'late.js'));
    assert.equal(late.later, undefined);
    registerLate({ later: () => 'resolved at call time' });
    assert.equal(late.later(), 'resolved at call time');
});

test('the old single file is gone and nothing still loads it', () => {
    assert.ok(!readdirSync(STATIC_JS).includes('chat.js'));
    const index = readFileSync(join(STATIC_JS, '..', '..', 'templates', 'index.html'), 'utf8');
    assert.ok(!index.includes("js/chat.js'"));
    assert.match(index, /<script type="module" src="\{\{ asset\('js\/chat\/main\.js'\) \}\}"><\/script>/);
});
