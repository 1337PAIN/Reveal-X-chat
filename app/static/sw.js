const CACHE_NAME = 'revealx-cache-v12-logo';
// The HTML pages are deliberately NOT precached. An app shell held in the
// cache is the classic reason a deploy "needs a hard refresh": the page is
// served from cache, still listing the old scripts, so nothing new is ever
// requested. They are still cached at runtime by the fetch handler below, so
// offline use works -- they are just never preferred over the network.
const ASSETS_TO_CACHE = [
    '/static/css/style.css',
    '/static/css/glass.css',
    '/static/css/settings.css',
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
self.addEventListener('fetch', (event) => {
    // Avoid caching Socket.IO long-polling requests
    if (event.request.url.includes('/socket.io/')) {
        return;
    }

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
                if (response && response.status === 200 && event.request.method === 'GET') {
                    const responseToCache = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseToCache);
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
