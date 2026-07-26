/* WhisperDeck Service Worker — static asset caching + offline shell
   Served from /sw.js (root scope), NOT /static/sw.js.
   Bump CACHE_VERSION on deploy to invalidate. */

const CACHE_VERSION = 'v1';
const CACHE_NAME = 'whisperdeck-static-' + CACHE_VERSION;

// Static assets precached on install. Root '/' is the SPA shell (index.html).
const PRECACHE = [
  '/',
  '/static/rack.js',
  '/static/rack.css',
  '/static/fonts/barlow-400.woff2',
  '/static/fonts/barlow-500.woff2',
  '/static/fonts/barlow-600.woff2',
  '/static/fonts/barlow-condensed-600.woff2',
  '/static/fonts/barlow-condensed-700.woff2',
  '/static/fonts/ibm-plex-mono-500.woff2',
  '/static/fonts/ibm-plex-mono-700.woff2',
  '/static/fonts/share-tech-mono-400.woff2',
];

/* ── install: precache static assets ── */
self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      // addAll would fail the entire batch on a single failure, so do each
      // individually — the worker still activates with whatever cached ok.
      return Promise.allSettled(PRECACHE.map(function (url) {
        return cache.add(url).catch(function (err) {
          console.warn('sw: failed to precache ' + url, err);
        });
      }));
    })
  );
  self.skipWaiting();
});

/* ── activate: purge old caches, claim clients ── */
self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (k) { return k !== CACHE_NAME; })
            .map(function (k) { return caches.delete(k); })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

/* ── fetch: cache-first for static, network-first for API ── */
self.addEventListener('fetch', function (e) {
  var url = new URL(e.request.url);
  var path = url.pathname;

  // API calls: network-first with cache fallback (stale data beats nothing).
  if (path.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request).catch(function () {
        return caches.match(e.request);
      })
    );
    return;
  }

  // Everything else: cache-first, falling back to network.
  e.respondWith(
    caches.match(e.request).then(function (cached) {
      return cached || fetch(e.request);
    })
  );
});
