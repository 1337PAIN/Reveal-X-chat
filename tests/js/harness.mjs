/**
 * Load a browser script into Node so it can be unit tested.
 *
 * The scripts under app/static/js are classic scripts, not modules: they define
 * things at the top level and hang the public surface off `window`. Rather than
 * restructure working security code to suit a test runner -- which would be
 * changing the thing under test to make testing easier -- the source is read
 * verbatim and evaluated with the globals it expects supplied as parameters.
 *
 * That means these tests run the *shipped* file. No build step, no duplicated
 * logic to drift, and a bug fixed here is a bug fixed in what users load.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
export const STATIC_JS = resolve(HERE, '..', '..', 'app', 'static', 'js');
export const STATIC_ROOT = resolve(HERE, '..', '..', 'app', 'static');

/**
 * Evaluate `file` with `globals` in scope. Returns the globals object, so a
 * script that does `window.foo = ...` leaves `foo` on the window you passed in.
 */
export function loadBrowserScript(file, globals = {}) {
    const source = readFileSync(file, 'utf8');
    const names = Object.keys(globals);
    // eslint-disable-next-line no-new-func
    const factory = new Function(...names, `'use strict';\n${source}`);
    factory(...names.map((name) => globals[name]));
    return globals;
}

/** A `window` carrying the Web Crypto and encoding globals these scripts use. */
export function makeWindow(extra = {}) {
    const win = {
        crypto: globalThis.crypto,
        TextEncoder: globalThis.TextEncoder,
        TextDecoder: globalThis.TextDecoder,
        ...extra,
    };
    win.window = win;
    return win;
}

/**
 * Minimal IndexedDB, enough for the key store in e2ee.js.
 *
 * Values are held as-is rather than structured-cloned. Real IndexedDB can clone
 * a CryptoKey, and cloning it here would be reimplementing the browser to no
 * purpose -- what these tests care about is that the right key comes back out
 * under the right id, not that Node can serialise it.
 */
export function makeIndexedDB() {
    const databases = new Map();

    // Requests resolve asynchronously, as the real ones do. A shim that called
    // back synchronously would let code pass here that deadlocks in a browser.
    const request = (produce) => {
        const req = { onsuccess: null, onerror: null, onupgradeneeded: null, result: undefined, error: null };
        queueMicrotask(() => {
            try {
                req.result = produce(req);
                if (req.onsuccess) req.onsuccess();
            } catch (error) {
                req.error = error;
                if (req.onerror) req.onerror();
            }
        });
        return req;
    };

    return {
        open(name) {
            let db = databases.get(name);
            const isNew = !db;
            if (isNew) {
                db = { name, stores: new Map() };
                databases.set(name, db);
            }
            const handle = {
                get objectStoreNames() {
                    return { contains: (store) => db.stores.has(store) };
                },
                createObjectStore(store) {
                    db.stores.set(store, new Map());
                    return {};
                },
                transaction(storeName, _mode) {
                    return {
                        objectStore() {
                            const data = db.stores.get(storeName);
                            return {
                                get: (key) => request(() => data.get(key)),
                                put: (value, key) => request(() => {
                                    data.set(key, value);
                                    return key;
                                }),
                                delete: (key) => request(() => data.delete(key)),
                            };
                        },
                    };
                },
            };
            // Order matters: the real API fires onupgradeneeded first, with
            // request.result already set, and only then onsuccess. Queueing them
            // separately would invert that and let code pass here that finds no
            // object store in a browser.
            const req = { onsuccess: null, onerror: null, onupgradeneeded: null, result: undefined, error: null };
            queueMicrotask(() => {
                try {
                    req.result = handle;
                    if (isNew && req.onupgradeneeded) req.onupgradeneeded();
                    if (req.onsuccess) req.onsuccess();
                } catch (error) {
                    req.error = error;
                    if (req.onerror) req.onerror();
                }
            });
            return req;
        },
        /** Test helper: forget everything, so a case can start from no keys. */
        _reset() {
            databases.clear();
        },
    };
}
