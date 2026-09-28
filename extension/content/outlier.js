/* CreatorHaven — content/outlier.js  (www.youtube.com, isolated world, document_start)
 *
 * Shows an outlier multiplier (e.g. 2.3×) next to every video title while you browse — the
 * video's view count ÷ the channel's median views. The median (baseline) is fetched from the
 * CreatorHaven site (/api/ext/baselines), batched and cached 24 h in storage.local so repeat
 * browsing costs nothing. Tracked channels are free on the server; others cost a few quota
 * units the first time they're seen.
 *
 * Same discipline as content/yt.js: gated by its toggle, idempotent (marks cards it touched),
 * re-run on a debounced MutationObserver + SPA navigation, every path wrapped in try/catch.
 */
(() => {
  'use strict';

  const api = globalThis.browser ?? globalThis.chrome;
  const SF = globalThis.SF;
  const TAG = '[SF outlier]';
  if (!SF || !api || !api.runtime || !api.storage) return;

  let settings = SF.withDefaults({});
  const warned = new Set();
  const warn = (scope, e) => { const k = scope + ':' + (e && e.message ? e.message : String(e)); if (!warned.has(k)) { warned.add(k); console.warn(TAG, scope, e); } };
  const safe = (scope, fn) => { try { return fn(); } catch (e) { warn(scope, e); } };

  /* --------------------------------------------------------------- number parsing */
  const parseCount = (s) => {
    const m = String(s || '').replace(/,/g, '').trim().match(/([\d.]+)\s*([KMB])?/i);
    if (!m) return null;
    let v = parseFloat(m[1]); if (!isFinite(v)) return null;
    const u = (m[2] || '').toUpperCase();
    if (u === 'K') v *= 1e3; else if (u === 'M') v *= 1e6; else if (u === 'B') v *= 1e9;
    return Math.round(v);
  };
  const viewsFromText = (t) => {
    if (!t) return null;
    if (/\bno views\b/i.test(t)) return 0;
    const m = String(t).match(/([\d.,]+\s*[KMB]?)\s*views/i);
    return m ? parseCount(m[1]) : null;
  };

  const site = () => SF.normalizeSiteUrl(settings.siteUrl);

  /* --------------------------------------------------------------- baseline cache + batch */
  // Badges appear within a second or two: small batches go out as soon as cards are seen,
  // several at once, in page order (top of the feed first). A channel with a real baseline is
  // cached 24 h; "no baseline" only 1 h; a FAILED request (site off, network, bad token) is
  // never cached — it is retried with backoff instead of hiding that channel's badges all day.
  const BASE_TTL = 24 * 3600 * 1000, EMPTY_TTL = 3600 * 1000;
  const BATCH = 12, PARALLEL = 3, FIRST_DELAY = 120;
  const RETRY_MS = [4000, 15000, 60000];
  const mem = new Map();              // ident -> {t, base}
  const pending = new Map();          // ident -> [cb, ...]
  let queue = new Set();
  let flushT = 0, inflight = 0, failures = 0;

  const ttl = (rec) => (rec && rec.base && (rec.base.median_long || rec.base.median)) ? BASE_TTL : EMPTY_TTL;
  const fresh = (rec) => rec && (Date.now() - rec.t) < ttl(rec);
  const getBase = (ident) => { const r = mem.get(ident); return fresh(r) ? r.base : undefined; };

  async function loadStored(idents) {
    try {
      const keys = idents.map((i) => 'sfbl:' + i);
      const got = await api.storage.local.get(keys);
      for (const i of idents) { const r = got['sfbl:' + i]; if (fresh(r)) mem.set(i, r); }
    } catch (e) { /* storage.local may be unavailable; fall through to fetch */ }
  }

  function kick(delay) {
    if (flushT) return;               // a flush is already scheduled; never push it back
    flushT = setTimeout(() => { flushT = 0; safe('flush', pump); }, delay);
  }

  function requestBase(ident, cb) {
    const have = getBase(ident);
    if (have !== undefined) { cb(have); return; }
    if (!pending.has(ident)) pending.set(ident, []);
    pending.get(ident).push(cb);
    queue.add(ident);
    kick(FIRST_DELAY);
  }

  function pump() {
    while (inflight < PARALLEL && queue.size) {
      const idents = [...queue].slice(0, BATCH);
      for (const i of idents) queue.delete(i);
      inflight++;
      flush(idents).catch((e) => warn('flush', e)).finally(() => { inflight--; if (queue.size) kick(0); });
    }
  }

  function deliver(i, base) {
    const cbs = pending.get(i) || []; pending.delete(i);
    cbs.forEach((cb) => safe('cb', () => cb(base)));
  }

  async function flush(idents) {
    await loadStored(idents);
    const need = idents.filter((i) => getBase(i) === undefined);
    for (const i of idents) if (!need.includes(i)) deliver(i, getBase(i));
    if (!need.length) return;
    let res = null;
    try {
      const r = await fetch(site() + '/api/ext/baselines', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-SF-Token': settings.token || '' },
        body: JSON.stringify({ channels: need })
      });
      if (r.ok) res = (await r.json()).baselines || {};
      else warn('fetch', 'HTTP ' + r.status);
    } catch (e) { warn('fetch', e); }
    if (!res) {                               // failed: keep the callbacks, retry later, cache nothing
      const wait = RETRY_MS[Math.min(failures, RETRY_MS.length - 1)]; failures++;
      setTimeout(() => { for (const i of need) queue.add(i); kick(0); }, wait);
      return;
    }
    failures = 0;
    const toStore = {};
    for (const i of need) {
      const b = res[i];
      const base = (b && !b.error) ? b : null;
      if (b && b.error) { deliver(i, null); continue; }   // a server-side hiccup for one channel: don't cache it
      const rec = { t: Date.now(), base };
      mem.set(i, rec); toStore['sfbl:' + i] = rec;
      deliver(i, base);
    }
    try { if (Object.keys(toStore).length) await api.storage.local.set(toStore); } catch (e) { /* ignore */ }
  }

  /* --------------------------------------------------------------- DOM */
  const CARD_SEL = [
    'ytd-rich-item-renderer', 'ytd-video-renderer', 'ytd-grid-video-renderer',
    'ytd-compact-video-renderer', 'ytd-rich-grid-media', 'ytd-playlist-video-renderer'
  ].join(',');

  const pageChannel = () => {
    const uc = location.pathname.match(/\/channel\/(UC[0-9A-Za-z_-]{20,})/);
    if (uc) return uc[1];
    const at = location.pathname.match(/\/(@[A-Za-z0-9._-]+)/);
    return at ? at[1] : null;
  };

  function cardChannel(card) {
    const a = card.querySelector('a[href*="/channel/UC"], a[href^="/@"], ytd-channel-name a, #channel-name a, .ytd-channel-name a');
    if (a) {
      const h = a.getAttribute('href') || '';
      const uc = h.match(/\/channel\/(UC[0-9A-Za-z_-]{20,})/);
      if (uc) return uc[1];
      const at = h.match(/\/(@[A-Za-z0-9._-]+)/);
      if (at) return at[1];
    }
    return pageChannel();     // grid cards on a channel page carry no channel link
  }

  // Metadata text nodes across layouts: old (#metadata-line / inline-metadata-item),
  // the -wiz__ lockup, and the CURRENT camelCase lockup (ytContentMetadataViewModelMetadataText).
  const META_SEL = '#metadata-line span, .inline-metadata-item, #metadata span, .ytd-video-meta-block span, ' +
    '.yt-content-metadata-view-model-wiz__metadata-text, .ytContentMetadataViewModelMetadataText';

  function cardViews(card) {
    for (const s of card.querySelectorAll(META_SEL)) { const v = viewsFromText(s.textContent); if (v !== null) return v; }
    const t = titleEl(card);
    if (t) { const v = viewsFromText(t.getAttribute('aria-label') || t.getAttribute('title')); if (v !== null) return v; }
    // last resort: any leaf element on the card that mentions "views"
    for (const e of card.querySelectorAll('span, div')) {
      if (!e.firstElementChild) { const v = viewsFromText(e.textContent); if (v !== null) return v; }
    }
    return null;
  }

  const titleEl = (card) => card.querySelector(
    '#video-title, a#video-title-link, #video-title-link, .yt-lockup-metadata-view-model-wiz__title, a.ytLockupMetadataViewModelTitle'
  );

  // Where the outlier bubble goes: the metadata row holding the view count (new camelCase
  // lockup), else the -wiz__ row, else the classic channel byline. Placed by the views so it
  // reads "… · 1.2M views · 3d  2.3×" while scrolling the feed.
  function bylineHost(card) {
    const rows = card.querySelectorAll('.ytContentMetadataViewModelMetadataRow, .yt-content-metadata-view-model-wiz__metadata-row');
    for (const r of rows) { if (/\bviews\b/i.test(r.textContent || '')) return r; }
    return card.querySelector('ytd-channel-name #text, ytd-channel-name yt-formatted-string, #channel-name #text')
      || card.querySelector('.yt-content-metadata-view-model-wiz__metadata-row .yt-core-attributed-string')
      || rows[0] || null;
  }

  const fmt = (r) => r >= 10 ? Math.round(r) + '×'
    : r >= 1 ? r.toFixed(1).replace(/\.0$/, '') + '×'
      : r.toFixed(2) + '×';

  // A heat scale: the bigger the outlier the hotter and more filled the bubble.
  const tierOf = (r) => r >= 10 ? 'x10' : r >= 5 ? 'x5' : r >= 3 ? 'x3'
    : r >= 2 ? 'x2' : r >= 1.2 ? 'up' : r >= 0.8 ? 'ok' : 'low';

  function render(b, ratio) {
    const txt = fmt(ratio);
    b.textContent = txt;
    b.dataset.tier = tierOf(ratio);
    b.title = 'CreatorHaven outlier — ' + txt + " the channel's median views";
  }

  function placeBadge(card) {
    // Prefer the channel byline (beside the sub count); fall back to the title so the badge
    // still shows on layouts where the byline can't be found.
    const host = bylineHost(card) || titleEl(card);
    if (!host) return null;
    let b = host.querySelector(':scope > .sf-ol-badge') || card.querySelector('.sf-ol-badge');
    if (b && b.parentElement !== host) { b.remove(); b = null; }
    if (!b) { b = document.createElement('span'); b.className = 'sf-ol-badge'; }
    const subs = host.querySelector('.sf-subs');     // yt.js sub-count pill, if present
    if (subs && subs.nextSibling !== b) host.insertBefore(b, subs.nextSibling);
    else if (!b.parentElement) host.appendChild(b);
    return b;
  }

  function process(card) {
    if (!titleEl(card) && !bylineHost(card)) return;
    const views = cardViews(card);
    const ident = cardChannel(card);
    if (views == null || !ident) return;
    const sig = ident + '|' + views;
    if (card.dataset.sfOl === sig && card.querySelector('.sf-ol-badge')) return;   // already done, unchanged
    card.dataset.sfOl = sig;
    requestBase(ident, (base) => {
      if (card.dataset.sfOl !== sig) return;                 // card was recycled while we waited
      const med = base && (base.median_long || base.median);
      const existing = card.querySelector('.sf-ol-badge');
      if (!med) { if (existing) existing.remove(); return; }
      const b = placeBadge(card);
      if (b) render(b, views / med);
    });
  }

  function clearAll() {
    document.querySelectorAll('.sf-ol-badge').forEach((b) => b.remove());
    document.querySelectorAll('[data-sf-ol]').forEach((c) => { delete c.dataset.sfOl; });
  }

  /* --------------------------------------------------------------- run loop */
  // THROTTLE, not debounce: YouTube's DOM changes many times a second (hover previews, lazy
  // thumbnails, live counters), so a debounce that restarts on every mutation could wait
  // minutes for a quiet moment. This runs within 150 ms of the first change and at most every
  // 400 ms while the page keeps changing.
  let scanT = 0, lastScan = 0;
  const scan = () => { lastScan = Date.now(); if (settings.youtubeOutliers) document.querySelectorAll(CARD_SEL).forEach((c) => safe('process', () => process(c))); };
  const schedule = () => {
    if (scanT) return;
    const wait = Math.max(150, 400 - (Date.now() - lastScan));
    scanT = setTimeout(() => { scanT = 0; safe('scan', scan); }, wait);
  };

  const mo = new MutationObserver(schedule);
  let lastHref = location.href;
  function start() {
    safe('observe', () => mo.observe(document.documentElement, { childList: true, subtree: true }));
    document.addEventListener('yt-navigate-finish', schedule);
    document.addEventListener('yt-page-data-updated', schedule);
    setInterval(() => { if (location.href !== lastHref) { lastHref = location.href; schedule(); } }, 1000);
    schedule();
  }

  if (api.storage.onChanged) api.storage.onChanged.addListener((changes, area) => {
    if (area !== 'sync') return;
    const was = settings.youtubeOutliers;
    safe('reload', async () => {
      settings = SF.withDefaults(await api.storage.sync.get(null));
      if (was && !settings.youtubeOutliers) clearAll();
      else schedule();
    });
  });

  safe('boot', async () => {
    const raw = await api.storage.sync.get(null);
    settings = SF.withDefaults(raw);
    start();
  });
})();
