/* CreatorHaven service worker: makes the site installable and opens it instantly (and offline)
   from the home screen. Pages and static files are network-first, so a redeploy shows up on the
   next open; every API call goes straight to the network (the data is live). Bump CACHE on
   shell changes. v3: the phone-only /m app is gone, the full site is the app. */
const CACHE = 'ch-v3';
const SHELL = ['/', '/static/icon-192.png', '/static/icon-512.png', '/manifest.webmanifest'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/thumbs/')) return;   // always live
  if (url.pathname === '/' || url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
      return r;
    }).catch(() => caches.match(e.request)));
  }
});
