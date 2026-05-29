const CACHE_NAME = 'revealx-cache-v1';
const ASSETS_TO_CACHE = [
    '/',
    '/static/css/style.css',
    '/static/js/chat.js',
    '/static/js/crypto.js',
    '/static/Xlogo.png',
    'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
    'https://cdn.jsdelivr.net/npm/dompurify@3.0.6/purify.min.js'
];

// Install Event
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(ASSETS_TO_CACHE);
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

    event.respondWith(
        fetch(event.request)
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
