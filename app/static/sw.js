const CACHE_NAME = 'revealx-cache-v14-sanitize';
// The HTML pages are deliberately NOT precached. An app shell held in the
// cache is the classic reason a deploy "needs a hard refresh": the page is
// served from cache, still listing the old scripts, so nothing new is ever
// requested. They are still cached at runtime by the fetch handler below, so
// offline use works -- they are just never preferred over the network.
const ASSETS_TO_CACHE = [
    '/static/css/style.css',
    '/static/css/glass.css',
    '/static/css/settings.css',
    '/static/js/sanitize.js',
    '/static/js/chat.js',
    '/static/js/crypto.js',
    '/static/js/e2ee.js',
    '/static/js/firebase-auth.js',
    '/static/js/tamper-onnx.js',
    '/static/js/admin.js',
    // The ONNX graph and its metadata are small (~45 KB) and worth precaching.
    // The onnxruntime-web wasm binary is ~11 MB and deliberately NOT listed:
    // precaching it would cost every first-time visitor that download even if
    // they never open a share. The runtime fetch handler below caches it after
    // the first real use, which is when offline support starts to matter.
    '/static/models/tamper_detector.onnx',
    '/static/models/tamper_detector.meta.json',
    '/static/css/rx_lab.css',
    '/static/js/rx_lab.js',
    '/static/Xlogo.png',
    '/static/icon-192.png',
];

// Third-party assets are cached on a best-effort basis. They are deliberately
// NOT in ASSETS_TO_CACHE: cache.addAll() is atomic, so a single unreachable CDN
// would reject the whole install and leave the app with no offline cache at all
// -- silently, because nothing else breaks. Offline support that evaporates the
// moment a CDN is blocked is not offline support.
const OPTIONAL_ASSETS = [
    'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
    'https://cdn.jsdelivr.net/npm/dompurify@3.0.6/purify.min.js'
];

// Install Event
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(async (cache) => {
            // Same-origin assets must all land, or the cache is not usable.
            await cache.addAll(ASSETS_TO_CACHE);
            // Third-party ones individually, so one failure costs only itself.
            await Promise.all(OPTIONAL_ASSETS.map(
                (url) => cache.add(url).catch(() => {})
            ));
        }).then(() => self.skipWaiting())
    );
});

// Activate Event
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cache) => {
                    if (cache !== CACHE_NAME) {
                        return caches.delete(cache);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Fetch Event (Network First, Cache Fallback)
/* Third-party origins whose assets are worth keeping offline. Anything else
   cross-origin is left completely alone. */
const CACHEABLE_ORIGINS = [
    'https://fonts.googleapis.com',
    'https://fonts.gstatic.com',
    'https://cdnjs.cloudflare.com',
    'https://cdn.jsdelivr.net',
    'https://cdn.socket.io',
];

/**
 * Should this worker take over the request at all?
 *
 * Answering "no" means never calling respondWith(), which leaves the request
 * exactly as the browser would have made it. That matters more than it sounds:
 * a worker that proxies *everything* through fetch() re-issues cross-origin
 * requests itself, and sign-in flows do not survive that. Firebase's popup
 * sign-in talks to accounts.google.com, identitytoolkit.googleapis.com and an
 * iframe on <project>.firebaseapp.com, with redirects and credentials the
 * worker has no business replaying -- the visible symptom was
 * `auth/internal-error`, thrown only in browsers where the worker was
 * registered, which is why it never reproduced in a test harness.
 *
 * The rule: same-origin GETs, plus the CDNs above. Nothing else.
 */
function shouldHandle(request) {
    // A cache can only answer GETs, and a POST proxied through here is a POST
    // sent twice as far as any server-side effect is concerned.
    if (request.method !== 'GET') return false;

    let url;
    try {
        url = new URL(request.url);
    } catch (err) {
        return false;
    }

    // Socket.IO long-polling: never cacheable, and latency-sensitive.
    if (url.pathname.startsWith('/socket.io/')) return false;

    if (url.origin === self.location.origin) {
        // API responses are per-session. /api/auth/config in particular decides
        // whether the Google button appears; a cached copy from before Firebase
        // was configured would keep hiding it.
        if (url.pathname.startsWith('/api/')) return false;
        return true;
    }

    return CACHEABLE_ORIGINS.includes(url.origin);
}

self.addEventListener('fetch', (event) => {
    if (!shouldHandle(event.request)) return;

    // fetch() inside a worker still consults the browser's HTTP cache, so a
    // heuristically cached page could be returned without touching the
    // network -- "network first" in name only. For navigations, bypass it.
    const isNavigation = event.request.mode === 'navigate';
    const request = isNavigation
        ? new Request(event.request, { cache: 'no-store' })
        : event.request;

    event.respondWith(
        fetch(request)
            .then((response) => {
                // If valid network response, cache it (except POSTs)
                if (response && response.status === 200 && response.type !== 'opaque') {
                    const responseToCache = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        // Still guard the put: an opaque or partial response
                        // rejects here, and an unhandled rejection in a worker
                        // is invisible from the page.
                        cache.put(event.request, responseToCache).catch(() => {});
                    });
                }
                return response;
            })
            .catch(() => {
                // Fallback to cache on network failure
                return caches.match(event.request).then((cachedResponse) => {
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    // If root page fails and not in cache, fallback
                    if (event.request.mode === 'navigate') {
                        return caches.match('/');
                    }
                });
            })
    );
});
