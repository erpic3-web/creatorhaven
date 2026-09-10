/* CreatorHaven phone app service worker: cache the shell for instant opens and offline
   launches; every API call goes to the network (the data is live). Bump CACHE on shell changes. */
const CACHE = 'ch-m-v2';
const SHELL = ['/m', '/static/icon-192.png', '/static/icon-512.png', '/manifest.webmanifest'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/thumbs/')) return;   // always live
  if (url.pathname === '/m' || url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    // network first (so a redeploy shows up on the next open), cache as the offline fallback
    e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r; })
      .catch(() => caches.match(e.request)));
  }
});
