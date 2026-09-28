/* CreatorHaven — content/watchpanel.js  (www.youtube.com/watch, isolated world, document_start)
 *
 * A ViewStats-style panel pinned at the top of the right-hand column on any watch page. It
 * shows the video's views and, in tabs, its placement on a 1..10 scale for the channel, a
 * general channel overview, and an estimated-revenue band. All figures come from the
 * CreatorHaven site (/api/ext/video), which does the YouTube Data API lookups and caches them.
 *
 * Same discipline as the other content scripts: gated by its toggle, idempotent, re-run on a
 * debounced observer + SPA navigation, every path wrapped in try/catch. Collapsed state and the
 * active tab persist in storage.local.
 */
(() => {
  'use strict';
  const api = globalThis.browser ?? globalThis.chrome;
  const SF = globalThis.SF;
  const TAG = '[SF panel]';
  if (!SF || !api || !api.runtime || !api.storage) return;

  let settings = SF.withDefaults({});
  const warned = new Set();
  const warn = (s, e) => { const k = s + ':' + (e && e.message || e); if (!warned.has(k)) { warned.add(k); console.warn(TAG, s, e); } };
  const safe = (s, fn) => { try { return fn(); } catch (e) { warn(s, e); } };
  const site = () => SF.normalizeSiteUrl(settings.siteUrl);

  const TABS = [['views', 'Views'], ['ranking', 'Ranking'], ['overview', 'Overview'], ['revenue', 'Revenue']];
  let ui = null;                 // {root, body, tabs} once built
  let activeTab = 'views';
  let collapsed = false;
  let curVid = null;             // video id currently rendered / loading
  const cache = new Map();       // videoId -> report

  /* --------------------------------------------------------------- helpers */
  const num = (n) => (n == null || !isFinite(n)) ? '—' : SF.compactNumber(n);
  const full = (n) => (n == null || !isFinite(n)) ? '—' : Math.round(n).toLocaleString('en-US');
  const money = (n) => (n == null || !isFinite(n)) ? '—' : '$' + Math.round(n).toLocaleString('en-US');
  const videoId = () => { try { return new URL(location.href).searchParams.get('v'); } catch (e) { return null; } };
  const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

  function chip(label, value, cls) {
    const c = el('div', 'sf-vp-chip' + (cls ? ' ' + cls : ''));
    c.appendChild(el('div', 'sf-vp-chip-v', value));
    c.appendChild(el('div', 'sf-vp-chip-l', label));
    return c;
  }

  /* --------------------------------------------------------------- build the shell */
  function ensureUi() {
    if (ui && document.body.contains(ui.root)) return ui;
    const host = document.querySelector('#secondary-inner') || document.querySelector('#secondary');
    if (!host) return null;
    const root = el('div', 'sf-vpanel');
    root.dataset.collapsed = collapsed ? '1' : '0';

    const head = el('div', 'sf-vp-head');
    const brand = el('div', 'sf-vp-brand');
    brand.appendChild(el('span', 'sf-vp-dot'));
    brand.appendChild(el('span', 'sf-vp-name', 'CREATORHAVEN'));
    const sub = el('span', 'sf-vp-chan', '');
    brand.appendChild(sub);
    head.appendChild(brand);
    const chev = el('button', 'sf-vp-chev'); chev.type = 'button'; chev.title = 'Collapse';
    chev.textContent = '⌃';
    chev.addEventListener('click', () => safe('collapse', () => {
      collapsed = !collapsed; root.dataset.collapsed = collapsed ? '1' : '0';
      try { api.storage.local.set({ sfVpCollapsed: collapsed }); } catch (e) {}
    }));
    head.appendChild(chev);
    root.appendChild(head);

    const tabRow = el('div', 'sf-vp-tabs');
    const tabEls = {};
    for (const pair of TABS) {
      const key = pair[0], label = pair[1];
      const tb = el('button', 'sf-vp-tab', label); tb.type = 'button'; tb.dataset.tab = key;
      tb.addEventListener('click', () => safe('tab', () => setTab(key)));
      tabRow.appendChild(tb); tabEls[key] = tb;
    }
    root.appendChild(tabRow);

    const body = el('div', 'sf-vp-body');
    root.appendChild(body);

    host.insertBefore(root, host.firstChild);
    ui = { root: root, body: body, tabEls: tabEls, chan: sub };
    highlightTab();
    return ui;
  }

  function highlightTab() {
    if (!ui) return;
    for (const pair of TABS) ui.tabEls[pair[0]].dataset.on = (pair[0] === activeTab) ? '1' : '0';
  }
  function setTab(key) {
    activeTab = key; highlightTab();
    try { api.storage.local.set({ sfVpTab: key }); } catch (e) {}
    renderBody();
  }

  /* --------------------------------------------------------------- tab renderers */
  function fmtMul(r) {
    if (r == null) return '—';
    return r >= 10 ? Math.round(r) + '×' : r >= 1 ? r.toFixed(1).replace(/\.0$/, '') + '×' : r.toFixed(2) + '×';
  }
  function tierOf(r) {
    return r >= 10 ? 'x10' : r >= 5 ? 'x5' : r >= 3 ? 'x3' : r >= 2 ? 'x2' : r >= 1.2 ? 'up' : r >= 0.8 ? 'ok' : 'low';
  }

  function viewsTab(rep, body) {
    const v = rep.video || {};
    const big = el('div', 'sf-vp-big');
    big.appendChild(el('div', 'sf-vp-big-n', full(v.views)));
    const cap = el('div', 'sf-vp-big-l'); cap.textContent = 'views since published';
    if (rep.outlier != null) {
      const b = el('span', 'sf-vp-mul'); b.textContent = fmtMul(rep.outlier); b.dataset.tier = tierOf(rep.outlier);
      b.title = fmtMul(rep.outlier) + " the channel's median views";
      cap.appendChild(document.createTextNode('  '));
      cap.appendChild(b);
    }
    big.appendChild(cap);
    body.appendChild(big);
    const grid = el('div', 'sf-vp-grid');
    grid.appendChild(chip('per day', num(v.views_per_day)));
    grid.appendChild(chip('age', v.age_days == null ? '—' : (v.age_days >= 1 ? Math.round(v.age_days) + 'd' : '<1d')));
    grid.appendChild(chip('likes', num(v.likes)));
    grid.appendChild(chip('comments', num(v.comments)));
    grid.appendChild(chip('engagement', v.engagement == null ? '—' : v.engagement + '%'));
    grid.appendChild(chip('format', v.is_short ? 'Short' : 'Long'));
    body.appendChild(grid);
  }

  function rankingTab(rep, body) {
    const r = rep.rank;
    if (!r) { body.appendChild(el('div', 'sf-vp-note', channelHint(rep))); return; }
    const isShort = (rep.video || {}).is_short;
    const list = r.list || [];
    // header: this video's place among the sample, 1 = best (most-viewed)
    const head = el('div', 'sf-vp-rankhead');
    head.appendChild(el('span', 'sf-vp-rankn', r.position ? String(r.position) : '—'));
    head.appendChild(el('span', 'sf-vp-rankof', 'of ' + (list.length || r.sample) + '  ·  1 = best'));
    body.appendChild(head);
    body.appendChild(el('div', 'sf-vp-note',
      'Where this video ranks by views among the channel’s recent ' + (isShort ? 'Shorts' : 'uploads') + '.'));
    if (!list.length) return;
    const curId = (rep.video || {}).id;
    body.appendChild(el('div', 'sf-vp-rlabel', 'Top recent ' + (isShort ? 'Shorts' : 'videos')));
    const wrap = el('div', 'sf-vp-toplist');
    for (const it of list) {
      const row = el('a', 'sf-vp-toprow');
      row.href = 'https://www.youtube.com/watch?v=' + it.video_id;
      if (it.current || it.video_id === curId) row.dataset.cur = '1';
      row.appendChild(el('span', 'sf-vp-toprk', String(it.rank)));
      if (it.thumb) { const im = el('img', 'sf-vp-topthumb'); im.src = it.thumb; im.loading = 'lazy'; im.alt = ''; row.appendChild(im); }
      const t = el('span', 'sf-vp-toptitle', it.title); t.title = it.title;
      row.appendChild(t);
      row.appendChild(el('span', 'sf-vp-topviews', num(it.views)));
      wrap.appendChild(row);
    }
    body.appendChild(wrap);
  }

  function overviewTab(rep, body) {
    const c = rep.channel, m = rep.metrics || {};
    if (!c) { body.appendChild(el('div', 'sf-vp-note', channelHint(rep))); return; }
    const title = el('div', 'sf-vp-otitle');
    title.appendChild(el('span', 'sf-vp-oname', c.title || 'This channel'));
    if (c.subs != null) title.appendChild(el('span', 'sf-vp-osubs', num(c.subs) + ' subs'));
    body.appendChild(title);
    const grid = el('div', 'sf-vp-grid');
    grid.appendChild(chip('total views', num(c.views)));
    grid.appendChild(chip('videos', num(c.videos)));
    grid.appendChild(chip('avg views', num(m.avg_views)));
    grid.appendChild(chip('median views', num(m.median_views)));
    grid.appendChild(chip('uploads / wk', m.uploads_per_week == null ? '—' : m.uploads_per_week));
    grid.appendChild(chip('shorts share', m.shorts_share == null ? '—' : m.shorts_share + '%'));
    grid.appendChild(chip('views / sub', m.views_per_sub == null ? '—' : m.views_per_sub));
    const yrs = c.age_days ? (c.age_days / 365) : null;
    grid.appendChild(chip('channel age', yrs == null ? '—' : (yrs >= 1 ? yrs.toFixed(1) + 'y' : Math.round(c.age_days) + 'd')));
    body.appendChild(grid);
  }

  function revenueTab(rep, body) {
    const r = rep.revenue;
    if (!r) { body.appendChild(el('div', 'sf-vp-note', channelHint(rep))); return; }
    const big = el('div', 'sf-vp-big');
    big.appendChild(el('div', 'sf-vp-big-n', money(r.low) + ' – ' + money(r.high)));
    big.appendChild(el('div', 'sf-vp-big-l', 'estimated earnings  •  ~' + money(r.mid) + ' mid'));
    body.appendChild(big);
    body.appendChild(el('div', 'sf-vp-note',
      'Rough estimate from views × RPM ($' + r.rpm_low + '–$' + r.rpm_high +
      ' per 1k, ' + r.basis + '). Real earnings depend on niche, geography, ad fill and Premium ' +
      'watch time — treat this as a ballpark, not a payout figure.'));
  }

  function channelHint(rep) {
    if (rep && rep.error === 'no_api') return 'Add a YouTube API key (or link a channel) on CreatorHaven to see channel data here.';
    if (rep && rep._netfail) return 'Can’t reach CreatorHaven at ' + site() + ' — check the site URL and token in the extension popup.';
    return 'Channel data unavailable for this video.';
  }

  function renderBody() {
    if (!ui) return;
    const body = ui.body; body.textContent = '';
    const rep = cache.get(curVid);
    if (!rep) { body.appendChild(el('div', 'sf-vp-note', 'Loading…')); return; }
    if (ui.chan) ui.chan.textContent = (rep.channel && rep.channel.title) ? rep.channel.title : '';
    safe('render:' + activeTab, () => {
      if (activeTab === 'views') viewsTab(rep, body);
      else if (activeTab === 'ranking') rankingTab(rep, body);
      else if (activeTab === 'overview') overviewTab(rep, body);
      else revenueTab(rep, body);
    });
  }

  /* --------------------------------------------------------------- fetch */
  async function load(vid) {
    if (cache.has(vid)) { renderBody(); return; }
    let rep;
    try {
      const r = await fetch(site() + '/api/ext/video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-SF-Token': settings.token || '' },
        body: JSON.stringify({ videoId: vid })
      });
      const j = await r.json().catch(() => ({}));
      rep = (j && j.ok) ? j : { video: {}, error: (j && j.error) || 'error' };
    } catch (e) { warn('fetch', e); rep = { video: {}, _netfail: true }; }
    cache.set(vid, rep);
    if (curVid === vid) renderBody();
  }

  /* --------------------------------------------------------------- run loop */
  function tick() {
    if (!settings.youtubePanel) { if (ui) { ui.root.remove(); ui = null; } return; }
    if (!location.pathname.startsWith('/watch')) { if (ui) { ui.root.remove(); ui = null; } return; }
    const vid = videoId();
    if (!vid) return;
    if (!ensureUi()) return;                 // secondary column not in the DOM yet; observer will retry
    if (vid !== curVid) { curVid = vid; renderBody(); safe('load', () => load(vid)); }
  }

  let t = 0;
  const schedule = () => { clearTimeout(t); t = setTimeout(() => safe('tick', tick), 250); };
  const mo = new MutationObserver(schedule);

  function start() {
    safe('observe', () => mo.observe(document.documentElement, { childList: true, subtree: true }));
    document.addEventListener('yt-navigate-finish', schedule);
    document.addEventListener('yt-page-data-updated', schedule);
    let last = location.href;
    setInterval(() => { if (location.href !== last) { last = location.href; schedule(); } }, 1000);
    schedule();
  }

  if (api.storage.onChanged) api.storage.onChanged.addListener((changes, area) => {
    if (area !== 'sync') return;
    safe('reload', async () => { settings = SF.withDefaults(await api.storage.sync.get(null)); schedule(); });
  });

  safe('boot', async () => {
    settings = SF.withDefaults(await api.storage.sync.get(null));
    try {
      const loc = await api.storage.local.get(['sfVpCollapsed', 'sfVpTab']);
      collapsed = !!loc.sfVpCollapsed;
      if (loc.sfVpTab && TABS.some((p) => p[0] === loc.sfVpTab)) activeTab = loc.sfVpTab;
    } catch (e) {}
    start();
  });
})();
