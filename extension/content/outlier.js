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
  const BASE_TTL = 24 * 3600 * 1000;
  const mem = new Map();              // ident -> {t, base}
  const pending = new Map();          // ident -> [cb, ...]
  let queue = new Set();
  let flushT = 0;

  const fresh = (rec) => rec && (Date.now() - rec.t) < BASE_TTL;
  const getBase = (ident) => { const r = mem.get(ident); return fresh(r) ? r.base : undefined; };

  async function loadStored(idents) {
    try {
      const keys = idents.map((i) => 'sfbl:' + i);
      const got = await api.storage.local.get(keys);
      for (const i of idents) { const r = got['sfbl:' + i]; if (fresh(r)) mem.set(i, r); }
    } catch (e) { /* storage.local may be unavailable; fall through to fetch */ }
  }

  function requestBase(ident, cb) {
    const have = getBase(ident);
    if (have !== undefined) { cb(have); return; }
    if (!pending.has(ident)) pending.set(ident, []);
    pending.get(ident).push(cb);
    queue.add(ident);
    clearTimeout(flushT); flushT = setTimeout(() => safe('flush', flush), 350);
  }

  async function flush() {
    const idents = [...queue].slice(0, 40);
    queue = new Set([...queue].slice(40));
    if (!idents.length) return;
    await loadStored(idents);
    const need = idents.filter((i) => getBase(i) === undefined);
    let res = {};
    if (need.length) {
      try {
        const r = await fetch(site() + '/api/ext/baselines', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-SF-Token': settings.token || '' },
          body: JSON.stringify({ channels: need })
        });
        if (r.ok) res = (await r.json()).baselines || {};
      } catch (e) { warn('fetch', e); }
    }
    const toStore = {};
    for (const i of idents) {
      let base = getBase(i);
      if (base === undefined) {                 // still unknown -> take the response (or null) and cache it
        base = (i in res) ? res[i] : null;
        const rec = { t: Date.now(), base };
        mem.set(i, rec); toStore['sfbl:' + i] = rec;
      }
      const cbs = pending.get(i) || []; pending.delete(i);
      cbs.forEach((cb) => safe('cb', () => cb(base)));
    }
    try { if (Object.keys(toStore).length) await api.storage.local.set(toStore); } catch (e) { /* ignore */ }
    if (queue.size) { clearTimeout(flushT); flushT = setTimeout(() => safe('flush', flush), 350); }
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

  function cardViews(card) {
    const spans = card.querySelectorAll('#metadata-line span, .inline-metadata-item, #metadata span, .ytd-video-meta-block span, .yt-content-metadata-view-model-wiz__metadata-text');
    for (const s of spans) { const v = viewsFromText(s.textContent); if (v !== null) return v; }
    const t = card.querySelector('#video-title, a#video-title-link, #video-title-link, .yt-lockup-metadata-view-model-wiz__title');
    if (t) { const v = viewsFromText(t.getAttribute('aria-label') || t.getAttribute('title')); if (v !== null) return v; }
    return null;
  }

  const titleEl = (card) => card.querySelector('#video-title, a#video-title-link, #video-title-link, .yt-lockup-metadata-view-model-wiz__title');

  function render(b, ratio) {
    const txt = ratio >= 100 ? Math.round(ratio) + '×'
      : ratio >= 10 ? ratio.toFixed(0) + '×'
        : ratio.toFixed(ratio >= 1 ? 1 : 2) + '×';
    b.textContent = txt;
    b.dataset.tier = ratio >= 5 ? 'fire' : ratio >= 2 ? 'hot' : ratio >= 0.8 ? 'ok' : 'cold';
    b.title = 'CreatorHaven outlier — ' + txt + " the channel's median views";
  }

  function process(card) {
    const el = titleEl(card);
    if (!el) return;
    const views = cardViews(card);
    const ident = cardChannel(card);
    if (views == null || !ident) return;
    const sig = ident + '|' + views;
    if (card.dataset.sfOl === sig && el.querySelector('.sf-ol-badge')) return;   // already done, unchanged
    card.dataset.sfOl = sig;
    requestBase(ident, (base) => {
      if (card.dataset.sfOl !== sig) return;                 // card was recycled while we waited
      const med = base && (base.median_long || base.median);
      let b = el.querySelector('.sf-ol-badge');
      if (!med) { if (b) b.remove(); return; }
      if (!b) { b = document.createElement('span'); b.className = 'sf-ol-badge'; el.appendChild(b); }
      render(b, views / med);
    });
  }

  function clearAll() {
    document.querySelectorAll('.sf-ol-badge').forEach((b) => b.remove());
    document.querySelectorAll('[data-sf-ol]').forEach((c) => { delete c.dataset.sfOl; });
  }

  /* --------------------------------------------------------------- run loop */
  let scanT = 0;
  const scan = () => { if (settings.youtubeOutliers) document.querySelectorAll(CARD_SEL).forEach((c) => safe('process', () => process(c))); };
  const schedule = () => { clearTimeout(scanT); scanT = setTimeout(() => safe('scan', scan), 300); };

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
