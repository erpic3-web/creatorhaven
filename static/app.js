/* CreatorHaven front-end: one page, six tabs, plain JS, inline SVG charts. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const reduceMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const state = { status: null, days: 28, chanDays: 28, channel: null, tool: null, columns: null };

  // ------------------------------------------------------------- helpers
  const api = async (path, opts = {}) => {
    const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts,
      body: opts.body && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body });
    let j = {};
    try { j = await r.json(); } catch (e) { /* non-json */ }
    if (r.status === 401 && path !== '/api/status') { showLogin(true); }
    if (!r.ok && !j.error) j.error = `HTTP ${r.status}`;
    return j;
  };
  const fmt = n => n == null ? '–' : (Math.abs(n) >= 1e9 ? (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B'
    : Math.abs(n) >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M'
    : Math.abs(n) >= 1e3 ? (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K' : String(Math.round(n)));
  const full = n => n == null ? '–' : Number(n).toLocaleString();
  const money = n => n == null ? '–' : '$' + Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
  const ago = ts => { if (!ts) return 'never'; const s = (Date.now() / 1000 - ts); return s < 90 ? 'just now' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 172800 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' d ago'; };
  const dateS = iso => iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '–';
  const dateT = iso => iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '–';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const dur = s => { if (!s) return '–'; const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; return (h ? h + ':' : '') + String(m).padStart(h ? 2 : 1, '0') + ':' + String(x).padStart(2, '0'); };
  const toast = msg => { const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden'); clearTimeout(t._t); t._t = setTimeout(() => t.classList.add('hidden'), 3500); };
  const showLogin = on => $('#login').classList.toggle('hidden', !on);

  // Inline SVG line chart: series = [{name, color, values:[{x,y}]}]
  function lineChart(el, series, opts = {}) {
    const W = el.clientWidth || 600, H = el.clientHeight || 220, P = { l: 44, r: 10, t: 10, b: 24 };
    const all = series.flatMap(s => s.values);
    if (!all.length) { el.innerHTML = '<p class="muted">No analytics data yet. Link the channel with the analytics scope and sync.</p>'; return; }
    const xs = [...new Set(all.map(v => v.x))].sort();
    const ymax = Math.max(1, ...all.map(v => v.y || 0)) * 1.08;
    const X = x => P.l + (xs.indexOf(x) / Math.max(1, xs.length - 1)) * (W - P.l - P.r);
    const Y = y => H - P.b - (y / ymax) * (H - P.t - P.b);
    let g = '';
    for (let i = 0; i <= 4; i++) { const y = ymax * i / 4; g += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${Y(y)}" y2="${Y(y)}" stroke="${cssVar('--line')}"/><text x="${P.l - 6}" y="${Y(y) + 4}" fill="${cssVar('--muted')}" font-size="10" text-anchor="end">${fmt(y)}</text>`; }
    const step = Math.ceil(xs.length / 6);
    xs.forEach((x, i) => { if (i % step === 0 || i === xs.length - 1) g += `<text x="${X(x)}" y="${H - 6}" fill="${cssVar('--muted')}" font-size="10" text-anchor="middle">${x.slice(5)}</text>`; });
    for (const s of series) {
      const pts = s.values.map(v => `${X(v.x)},${Y(v.y || 0)}`).join(' ');
      // thin, sleek 1px paths with zero fills that clip in on render (premium data-terminal look)
      g += `<polyline class="series anim" pathLength="1" fill="none" stroke="${s.color}" stroke-width="1.5" stroke-linejoin="round" points="${pts}"/>`;
    }
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}</svg>` +
      `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join('')}</div>`;
  }
  function barChart(el, series) {
    const W = el.clientWidth || 600, H = el.clientHeight || 220, P = { l: 44, r: 10, t: 10, b: 24 };
    const rows = series[0]?.values || [];
    if (!rows.length) { el.innerHTML = '<p class="muted">No data yet.</p>'; return; }
    const ymax = Math.max(1, ...series.flatMap(s => s.values.map(v => Math.abs(v.y || 0)))) * 1.1;
    const bw = (W - P.l - P.r) / rows.length;
    const Y = y => (H - P.b + P.t) / 2 - (y / ymax) * ((H - P.t - P.b) / 2);
    let g = `<line x1="${P.l}" x2="${W - P.r}" y1="${Y(0)}" y2="${Y(0)}" stroke="${cssVar('--line')}"/>`;
    series.forEach((s, si) => s.values.forEach((v, i) => { const y0 = Y(0), y1 = Y(v.y || 0); g += `<rect x="${P.l + i * bw + 1}" width="${Math.max(1, bw - 2)}" y="${Math.min(y0, y1)}" height="${Math.abs(y1 - y0)}" fill="${s.color}"/>`; }));
    const step = Math.ceil(rows.length / 6);
    rows.forEach((v, i) => { if (i % step === 0) g += `<text x="${P.l + i * bw + bw / 2}" y="${H - 6}" fill="${cssVar('--muted')}" font-size="10" text-anchor="middle">${v.x.slice(5)}</text>`; });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}</svg><div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join('')}</div>`;
  }
  const spark = (vals, color = cssVar('--trail')) => { if (!vals.length) return ''; const W = 200, H = 28, m = Math.max(1, ...vals); return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="1.5" points="${vals.map((v, i) => `${i / Math.max(1, vals.length - 1) * W},${H - (v / m) * (H - 2) - 1}`).join(' ')}"/></svg>`; };

  // ---------------------------------------------------------------- tabs
  $$('#tabs button').forEach(b => b.onclick = () => showTab(b.dataset.tab));
  // the account mark opens Settings (Settings lives off the visible nav strip)
  if ($('#acct')) $('#acct').onclick = () => showTab('settings');

  // -------------------------------------------------- theme: light / dark / system (persisted)
  const resolvedTheme = m => m === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : m;
  function paintTheme(mode) {
    document.documentElement.setAttribute('data-theme', mode);
    try { localStorage.setItem('sf_theme', mode); } catch (e) { }
    const tb = $('#themeBtn'); if (tb) tb.textContent = resolvedTheme(mode) === 'dark' ? 'Light' : 'Dark';
    $$('#themePick button').forEach(b => b.classList.toggle('on', b.dataset.themeMode === mode));
  }
  function redrawActive() {
    const a = document.querySelector('.tab.active'); if (!a) return;
    ({ 'tab-dashboard': loadDashboard, 'tab-lookup': loadLookupTab, 'tab-channels': loadChannelTab })[a.id]?.();
  }
  const setTheme = mode => { paintTheme(mode); redrawActive(); };
  if ($('#themeBtn')) $('#themeBtn').onclick = () => setTheme(resolvedTheme(localStorage.getItem('sf_theme') || 'system') === 'dark' ? 'light' : 'dark');
  $$('#themePick button').forEach(b => b.onclick = () => setTheme(b.dataset.themeMode));
  paintTheme(localStorage.getItem('sf_theme') || 'system');

  // background preset (Cloudline / Glacier / Velvet / Aurora), persisted
  function paintBg(mode) {
    document.documentElement.setAttribute('data-bg', mode);
    try { localStorage.setItem('sf_bg', mode); } catch (e) { }
    $$('#bgPick button').forEach(b => b.classList.toggle('on', b.dataset.bgMode === mode));
  }
  $$('#bgPick button').forEach(b => b.onclick = () => paintBg(b.dataset.bgMode));
  paintBg(localStorage.getItem('sf_bg') || 'cloudline');

  // "paste it to CreatorHaven" target — saves copied Studio markup so we can wire exact controls
  if ($('#extMarkupSave')) $('#extMarkupSave').onclick = async () => {
    const v = ($('#extMarkup').value || '').trim();
    if (!v) { $('#extMarkupMsg').textContent = 'Paste the copied markup first.'; return; }
    $('#extMarkupMsg').textContent = 'Sending…';
    const j = await api('/api/dev/markup', { method: 'POST', body: { markup: v, label: 'ad control' } });
    $('#extMarkupMsg').textContent = j.ok ? `Sent (${j.len} chars) — thank you!` : (j.error || 'failed');
    if (j.ok) $('#extMarkup').value = '';
  };

  // count-up for hero numbers (respects reduced motion)
  function countUp(el, to, fmtFn, dur = 700) {
    if (reduceMotion() || !isFinite(to)) { el.textContent = fmtFn(to); return; }
    const t0 = performance.now();
    (function step(t) { const p = Math.min(1, (t - t0) / dur); const e = 1 - Math.pow(1 - p, 3); el.textContent = fmtFn(to * e); if (p < 1) requestAnimationFrame(step); })(t0);
  }
  function showTab(name) {
    $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    $$('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + name));
    document.body.classList.toggle('brain-active', name === 'studio' && (state.ssec || 'strategy') === 'strategy');
    document.body.classList.toggle('home-active', name === 'home');
    ({ home: loadHome, dashboard: loadDashboard, channels: loadChannelTab, studio: loadStudio, calendar: loadPlanner, alerts: loadAlerts, tools: loadTools, lookup: loadLookupTab, settings: loadSettings })[name]?.();
    location.hash = name;
  }

  // ------------------------------------------------- home (calm, ChatGPT-style landing)
  let homeInit = false;
  function loadHome() { if (!homeInit) { homeInit = true; wireHomeSearch(); renderHomeChips(); } setTimeout(() => $('#homeQ')?.focus(), 60); }
  function renderHomeChips() {
    const chips = [
      { t: 'Track a channel', act: () => $('#homeQ')?.focus() },
      { t: 'Ask the strategy brain', act: () => { showTab('studio'); setStudioSec('strategy'); } },
      { t: 'Predict a video', act: () => { showTab('studio'); setStudioSec('predict'); } },
      { t: 'Generate a thumbnail', act: () => { showTab('studio'); setStudioSec('generate'); } },
      { t: 'Analyze a video', act: () => { state.tool = 'video-analyzer'; showTab('tools'); } },
    ];
    const el = $('#homeChips'); if (!el) return;
    el.innerHTML = chips.map((c, i) => `<button class="home-chip" data-i="${i}" style="animation-delay:${i * 0.05}s">${c.t}</button>`).join('');
    $$('#homeChips .home-chip').forEach((b, i) => b.onclick = chips[i].act);
  }
  function wireHomeSearch() {
    const el = $('#homeQ'), res = $('#homeRes'); if (!el) return;
    let t = null, items = [];
    const hide = () => res.classList.add('hidden');
    async function run() {
      const q = el.value.trim(); if (q.length < 2) { hide(); return; }
      res.innerHTML = '<div class="qfoot">Searching…</div>'; res.classList.remove('hidden');
      const j = await api(`/api/search/channels?q=${encodeURIComponent(q)}`);
      items = j.results || [];
      if (!items.length) { res.innerHTML = `<div class="qfoot">${j.error ? esc(j.error) : 'No channels found.'}</div>`; return; }
      res.innerHTML = items.map((r, i) => `<div class="qrow" data-i="${i}"><img src="${esc(r.thumb || '')}" alt=""><div><div class="qt">${esc(r.title)}${r.tracked ? '<span class="tag">tracked</span>' : ''}</div><div class="qm">${esc(r.handle || '')} · ${r.hidden_subs ? 'hidden' : fmt(r.subscribers)} subs · ${fmt(r.views)} views</div></div></div>`).join('') + `<div class="qfoot">${j.cached ? 'cached' : (j.units || 0) + ' quota units'} · Enter opens the first result</div>`;
      $$('.qrow', res).forEach(x => x.onclick = () => openChannel(items[+x.dataset.i].channel_id));
      refreshStatus();
    }
    el.oninput = () => { clearTimeout(t); const q = el.value.trim(); if (q.length < 2) { hide(); return; } t = setTimeout(run, /^@|youtube\.com|^UC[\w-]{20,}/.test(q) ? 300 : 900); };
    el.onkeydown = e => { if (e.key === 'Enter') { clearTimeout(t); if (items.length) openChannel(items[0].channel_id); else run(); } else if (e.key === 'Escape') hide(); };
    const go = $('#homeGo'); if (go) go.onclick = () => { const q = el.value.trim(); if (!q) { el.focus(); return; } items.length ? openChannel(items[0].channel_id) : run(); };
  }

  // -------------------------------------------------------------- status
  async function refreshStatus() {
    const s = await api('/api/status');
    state.status = s;
    if (!s.authed) { showLogin(true); return s; }
    showLogin(false);
    const q = s.quota_today || {};
    $('#quota').textContent = `quota ${full(q.units || 0)} / 10,000`;
    $('#alertBadge').textContent = s.alerts_open || 0;
    $('#alertBadge').classList.toggle('hidden', !s.alerts_open);
    $('#syncBtn').textContent = s.sync_running ? '⟳ Syncing…' : '⟳ Sync';
    return s;
  }
  $('#pwgo').onclick = async () => { const j = await api('/api/login', { method: 'POST', body: { password: $('#pw').value } }); if (j.ok) { await refreshStatus(); showTab(location.hash.slice(1) || 'home'); } else $('#pwerr').textContent = j.error || 'no'; };
  $('#pw').onkeydown = e => { if (e.key === 'Enter') $('#pwgo').click(); };
  $('#syncBtn').onclick = async () => { const j = await api('/api/sync', { method: 'POST', body: {} }); toast(j.started ? 'Sync started' : 'Sync already running'); pollSync(); };
  async function pollSync() {
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 2500));
      const s = await api('/api/sync/status');
      $('#syncBtn').textContent = s.running ? '⟳ Syncing…' : '⟳ Sync';
      if (!s.running) { await refreshStatus(); const t = location.hash.slice(1) || 'home'; showTab(t); toast('Sync finished'); return; }
    }
  }

  // ----------------------------------------------------------- dashboard
  $$('#daysSeg button').forEach(b => b.onclick = () => { state.days = +b.dataset.days; $$('#daysSeg button').forEach(x => x.classList.toggle('on', x === b)); loadDashboard(); });
  async function loadDashboard() {
    const o = await api(`/api/overview?days=${state.days}`);
    if (o.error) return;
    const t = o.totals, c = o.combined;
    const analyticsChannels = o.channels.filter(x => x.analytics).length;
    // editorial masthead — a mono meta line, no greeting emoji / template
    const now = new Date();
    const stamp = now.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }).toLowerCase();
    const g = $('#greet'); if (g) g.innerHTML = `<h1>Your network, at a glance</h1><p><span class="g-meta">${c.channels} channel${c.channels === 1 ? '' : 's'} · last ${o.days} days · ${stamp}</span></p>`;
    // asymmetric launch: a wide editorial banner (two actions) + a dense stacked rail (three)
    const RAIL = [
      { tab: 'calendar', label: 'Ledger', sub: 'production pipeline', svg: '<path d="M4 5h16M4 10h16M4 15h16M4 20h16"/>' },
      { tab: 'tools', label: 'Tools', sub: '18 creator utilities', svg: '<circle cx="6" cy="7" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18" cy="7" r="1.5"/><circle cx="6" cy="17" r="1.5"/><circle cx="18" cy="17" r="1.5"/>' },
      { tab: 'alerts', label: 'Alerts', sub: 'outliers & milestones', svg: '<path d="M6 9a6 6 0 0 1 12 0v4l2 3H4l2-3z"/><path d="M9.5 19a2.5 2.5 0 0 0 5 0"/>' },
    ];
    const qa = $('#quickActions');
    if (qa) {
      qa.innerHTML = `
        <div class="launch-hero">
          <button class="lh-primary" data-goto="studio">
            <span class="lh-kick">Create</span>
            <span class="lh-title">Studio — ideas, predictions &amp; thumbnails</span>
            <span class="lh-sub">Brainstorm titles, predict retention and generate thumbnails, grounded in your real outliers.</span>
            <span class="arrow-slide">&#10142;</span>
          </button>
          <button class="lh-second" data-goto="lookup">
            <span class="lh-body"><span class="lh-t">Search any channel</span><span class="lh-s">subs · views · outliers, any creator</span></span>
            <span class="arrow-slide">&#10142;</span>
          </button>
        </div>
        <div class="launch-rail">
          ${RAIL.map(a => `<button class="lr-row" data-goto="${a.tab}"><span class="lr-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${a.svg}</svg></span><span class="lr-tx"><b>${a.label}</b><span>${a.sub}</span></span><span class="arrow-slide">&#10142;</span></button>`).join('')}
        </div>`;
      $$('[data-goto]', qa).forEach(b => b.onclick = () => showTab(b.dataset.goto));
    }
    // KPI cards: value + trend delta (2nd-half vs 1st-half of the window) + a micro sparkline.
    const col = k => o.series.map(d => +d[k] || 0);
    const halfPct = arr => { const h = Math.floor(arr.length / 2); if (h < 1) return null;
      const a = arr.slice(0, h).reduce((x, y) => x + y, 0), b = arr.slice(h).reduce((x, y) => x + y, 0);
      return a > 0 ? Math.round((b - a) / a * 100) : null; };
    const signed = v => (v >= 0 ? '+' : '') + fmt(v);
    const kpis = [
      { k: 'Subscribers', n: c.subscribers, fn: fmt, s: `${c.channels} channels` },
      { k: 'Lifetime views', n: c.views, fn: fmt, s: `${full(c.videos)} videos` },
      { k: `Views · ${o.days}d`, n: t.views, fn: fmt, s: analyticsChannels ? `${analyticsChannels}/${c.channels} channels reporting` : 'needs analytics scope', ser: col('views') },
      { k: `Engaged views · ${o.days}d`, n: t.engaged_views, fn: fmt, s: t.engaged_ratio != null ? `${Math.round(t.engaged_ratio * 100)}% of views` : 'YPP counts these', ser: col('engaged_views') },
      { k: `Watch hours · ${o.days}d`, n: t.minutes / 60, fn: fmt, s: 'estimated', ser: o.series.map(d => (+d.minutes || 0) / 60) },
      { k: `Net subs · ${o.days}d`, n: t.net_subs, fn: signed, s: `${fmt(t.subs_gained)} gained · ${fmt(t.subs_lost)} lost`, ser: o.series.map(d => (+d.subs_gained || 0) - (+d.subs_lost || 0)) },
      { k: `Revenue · ${o.days}d`, n: t.revenue || null, fn: money, s: t.revenue ? 'estimated, USD' : 'needs revenue scope', ser: col('revenue') },
    ];
    // colour-coded pips by metric family: audience=violet, reach=blue, engagement=cyan, time=amber, growth/money=emerald
    const kpiHue = [];   // Creator Ledger: no colour pips — one platinum trail per card
    $('#tiles').innerHTML = kpis.map((o2, i) => {
      const has = o2.ser && o2.ser.some(x => x);
      const dv = has ? halfPct(o2.ser) : null;
      const delta = dv == null ? '' : (() => {
        const cls = dv > 0 ? 'up' : dv < 0 ? 'down' : 'flat';
        const body = `${dv > 0 ? '+' : ''}${dv}%`;
        return `<div class="delta-base"><span class="delta ${cls}">[ ${body} Delta ]</span></div>`;
      })();
      const sp = has ? `<div class="kpispark">${spark(o2.ser.map(x => Math.max(0, x)), cssVar('--trail'))}</div>` : '';
      const val = o2.n == null ? '–' : o2.fn(o2.n);
      return `<div class="tile"><div class="kpihead"><div class="k">${o2.k}</div></div><div class="v" data-kpi="${i}">${val}</div><div class="sub">${o2.s}</div>${sp}${delta}</div>`;
    }).join('');
    $$('#tiles .v[data-kpi]').forEach(el => { const k = kpis[+el.dataset.kpi]; if (k.n != null && isFinite(k.n)) countUp(el, k.n, k.fn); });
    lineChart($('#chartMain'), [
      { name: 'views', color: cssVar('--trail'), values: o.series.map(d => ({ x: d.day, y: d.views })) },
      { name: 'engaged views', color: '#71717a', values: o.series.map(d => ({ x: d.day, y: d.engaged_views })) }], { area: true });
    // YouTube-Studio-style subscriber graph: the running cumulative NET subscribers over the
    // period (a smooth line + gradient area), not a red/green gained-vs-lost bar chart.
    let subCum = 0;
    lineChart($('#chartSubs'), [
      { name: 'net subscribers', color: cssVar('--trail'),
        values: o.series.map(d => ({ x: d.day, y: (subCum += (d.subs_gained || 0) - (d.subs_lost || 0)) })) }], { area: true });
    $('#chanCards').innerHTML = o.channels.map(ch => `<div class="card" data-id="${ch.channel_id}">
      <div class="t">${ch.thumb ? `<img src="${ch.thumb}" alt="">` : ''}${esc(ch.title)}</div>
      <div class="s">${fmt(ch.stats.subscribers)} subs · ${fmt(ch.stats.views)} views · ${full(ch.stats.videos)} videos</div>
      <div class="s">${ch.analytics ? `${o.days}d: ${fmt(ch.totals.views)} views · ${fmt(ch.totals.engaged_views)} engaged · ${ch.totals.net_subs >= 0 ? '+' : ''}${fmt(ch.totals.net_subs)} subs${ch.totals.revenue ? ' · ' + money(ch.totals.revenue) : ''}` : '<span class="warn">analytics scope missing → re-link</span>'}</div>
      <div class="s">${ch.sync_error ? `<span class="err">${esc(ch.sync_error).slice(0, 120)}</span>` : `synced ${ago(ch.last_sync)}`}</div></div>`).join('') || '<p class="muted">No channels yet. Settings → Link channel.</p>';
    $$('#chanCards .card').forEach(c => c.onclick = () => { state.channel = c.dataset.id; showTab('channels'); });
    const al = await api('/api/alerts');
    $('#dashAlerts').innerHTML = (al.alerts || []).slice(0, 8).map(alertHtml).join('') || '<p class="muted">All clear — you\'ll get alerted here when a tracked channel posts or hits an outlier.</p>';
    bindAlertButtons($('#dashAlerts'));
  }

  // ------------------------------------------------------------ channels
  $$('#chanDays button').forEach(b => b.onclick = () => { state.chanDays = +b.dataset.days; $$('#chanDays button').forEach(x => x.classList.toggle('on', x === b)); loadChannelDetail(); });
  $('#chanSearch').oninput = () => renderChanList();
  $('#chanSync').onclick = async () => { if (!state.channel) return; const j = await api('/api/sync', { method: 'POST', body: { channel_id: state.channel } }); toast(j.started ? 'Syncing channel…' : 'Sync already running'); pollSync(); };
  const COLS = [
    ['thumb', 'Video', v => `<img class="thumb" src="${v.thumb || ''}" alt=""><a href="https://studio.youtube.com/video/${v.video_id}/edit" target="_blank">${esc(v.title)}</a>`],
    ['published_at', 'Published', v => dateS(v.published_at)],
    ['privacy', 'Visibility', v => v.privacy === 'private' && v.publish_at ? `<span class="pill">scheduled ${dateT(v.publish_at)}</span>` : `<span class="pill">${v.privacy}</span>`],
    ['is_short', 'Format', v => v.live ? 'live' : v.is_short ? 'short' : 'video'],
    ['duration_s', 'Length', v => dur(v.duration_s)],
    ['views', 'Views', v => full(v.views)],
    ['views_28d', 'Views 28d', v => full(v.views_28d)],
    ['engaged_28d', 'Engaged 28d', v => full(v.engaged_28d)],
    ['likes', 'Likes', v => full(v.likes)],
    ['comments', 'Comments', v => full(v.comments)],
    ['like_rate', 'Like %', v => v.views ? (100 * (v.likes || 0) / v.views).toFixed(2) + '%' : '–'],
    ['outlier', 'Outlier', v => v.outlier == null ? '–' : `<span class="pill ${v.outlier >= 2 ? 'good' : v.outlier <= 0.5 ? 'bad' : ''}">${v.outlier}x</span>`],
    ['made_for_kids', 'MFK', v => v.made_for_kids ? '<span class="pill warn">kids</span>' : ''],
    ['yt_rating', 'Age', v => v.yt_rating ? '<span class="pill bad">18+</span>' : ''],
    ['tags', 'Tags', v => `<span class="tiny">${(v.tags || []).length}</span>`],
    ['description_len', 'Desc chars', v => full(v.description_len)],
  ];
  const DEFAULT_COLS = ['thumb', 'published_at', 'privacy', 'is_short', 'duration_s', 'views', 'engaged_28d', 'outlier', 'like_rate'];
  const savedCols = () => { try { return JSON.parse(localStorage.getItem('sf_cols')) || DEFAULT_COLS; } catch (e) { return DEFAULT_COLS; } };
  async function loadChannelTab() {
    const list = await api('/api/channels');
    state.channels = list.channels || [];
    if (!state.channels.length) { $('#chanList').innerHTML = ''; $('#chanTitle').textContent = 'Channels'; $('#chanBody').innerHTML = '<p class="muted">Add channels in Settings, then sync.</p>'; return; }
    if (!state.channel || !state.channels.find(c => c.channel_id === state.channel)) state.channel = state.channels[0].channel_id;
    renderChanList();
    loadChannelDetail();
  }
  function renderChanList() {
    const q = (($('#chanSearch') && $('#chanSearch').value) || '').trim().toLowerCase();
    const rows = (state.channels || [])
      .filter(c => !q || (c.title || '').toLowerCase().includes(q) || (c.handle || '').toLowerCase().includes(q))
      .sort((a, b) => (((b.stats || {}).subscribers) || 0) - (((a.stats || {}).subscribers) || 0));
    $('#chanList').innerHTML = rows.map(c => `<button class="chrow${c.channel_id === state.channel ? ' on' : ''}" data-id="${c.channel_id}" title="${esc(c.title || '')}">${c.thumb ? `<img src="${esc(c.thumb)}" alt="" loading="lazy">` : '<span class="ph"></span>'}<span class="meta"><span class="nm">${esc(c.title || '(untitled)')}</span><span class="sb">${fmt((c.stats || {}).subscribers)} subs</span></span></button>`).join('') || '<p class="muted tiny">No match.</p>';
    $$('#chanList .chrow').forEach(b => b.onclick = () => { if (b.dataset.id === state.channel) return; state.channel = b.dataset.id; renderChanList(); loadChannelDetail(); });
  }
  async function loadChannelDetail() {
    if (!state.channel) return;
    const d = await api(`/api/channels/${state.channel}?days=${state.chanDays}`);
    if (d.error) { $('#chanBody').innerHTML = `<p class="err">${esc(d.error)}</p>`; return; }
    const ch = d.channel, t = d.totals, s = ch.stats || {};
    $('#chanTitle').textContent = ch.title;
    const top28 = Object.fromEntries(d.top.map(x => [x.video_id, x]));
    const vids = d.videos.map(v => ({ ...v, views_28d: top28[v.video_id]?.views_28d, engaged_28d: top28[v.video_id]?.engaged_28d }));
    const rt = d.realtime || [];
    const pub = d.public || {};
    const g = d.growth || {}, gw = g.window;
    const analytics = !!(d.scopes.analytics && d.series && d.series.length);
    const lifetime = state.chanDays === 0;
    const usePublic = !analytics || lifetime;
    const winMap = { 28: '28d', 90: '3mo', 180: '6mo', 365: '1yr', 0: 'lifetime' };
    const win = winMap[state.chanDays] ?? (d.days + 'd');
    const cols = savedCols();
    const er = pub.est_revenue || d.est_revenue || { long: {}, short: {}, combined: {} };
    const rng = (o) => `${money(o.low)}<span class="rsep">–</span>${money(o.high)}`;
    const signed = n => n == null ? '–' : (n >= 0 ? '+' : '') + fmt(n);
    const capNote = pub.sample_capped ? ' · older not scanned' : '';
    const trueViews = (!lifetime && gw && gw.views != null) ? gw.views : null;
    const rtVal = rt.length ? fmt(Number(rt.find(r => /48/i.test(r.key))?.value) || Number(rt[0].value)) : '–';
    const rtSub = rt.length ? `${rt.length} values · ${ago(rt[0].captured_at)}` : 'needs the extension on Studio';
    // hero = the three headline numbers; the rest sits in a labelled "details" grid so the
    // block reads as a hierarchy instead of one undifferentiated wall of tiles.
    const heroTile = (k, v, sub) => `<div class="tile hero"><div class="k">${k}</div><div class="v">${v}</div><div class="sub">${sub}</div></div>`;
    const tile = (k, v, sub) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="sub">${sub}</div></div>`;
    const pubViewsSub = pub.views_source === 'lifetime' ? 'lifetime total'
      : pub.views_source === 'channel_wide' ? 'channel-wide · incl. residual' + (gw && gw.since ? ' · since ' + gw.since : '')
      : 'on ' + fmt(pub.uploads) + ' uploads · fills in as snapshots build';
    const exactHero =
      heroTile('Subscribers', fmt(s.subscribers), s.hidden_subs ? 'hidden publicly' : 'public count') +
      heroTile('Views · ' + win, fmt(t.views), 'exact ' + full(t.views)) +
      heroTile('Revenue · ' + win, t.revenue ? money(t.revenue) : '–', d.scopes.monetary ? 'estimated' : 'no revenue scope');
    const exactRest =
      tile('Engaged · ' + win, fmt(t.engaged_views), t.engaged_ratio != null ? Math.round(t.engaged_ratio * 100) + '% of views' : '–') +
      tile('Watch hours · ' + win, fmt(t.minutes / 60), full(t.likes) + ' likes · ' + full(t.comments) + ' comments') +
      tile('Net subs · ' + win, (t.net_subs >= 0 ? '+' : '') + fmt(t.net_subs), fmt(t.subs_gained) + ' gained · ' + fmt(t.subs_lost) + ' lost') +
      tile('Realtime', rtVal, rtSub);
    const publicHero =
      heroTile('Subscribers', fmt(s.subscribers), gw && gw.subscribers != null ? signed(gw.subscribers) + ' in ' + gw.days + 'd' : (s.hidden_subs ? 'hidden publicly' : 'public count')) +
      heroTile('Views · ' + win, fmt(pub.views), pubViewsSub) +
      heroTile('Est. revenue · ' + win, rng(er.combined), 'estimate · ' + (lifetime ? 'lifetime' : win));
    const publicRest =
      tile('Likes · ' + win, fmt(pub.likes), pub.engagement_rate != null ? (pub.engagement_rate * 100).toFixed(1) + '% engagement' : '–') +
      tile('Comments · ' + win, fmt(pub.comments), fmt(pub.avg_views) + ' avg views/video') +
      tile('Watch hours · ' + win, fmt(pub.watch_hours), 'est · ~' + Math.round((pub.retention || 0.5) * 100) + '% retention') +
      tile('Views/day · ' + win, fmt(pub.views_per_day), pub.uploads_per_week != null ? pub.uploads_per_week + ' uploads/week' : 'pace') +
      tile('Uploads · ' + win, fmt(pub.uploads), (pub.uploads_long || 0) + ' long · ' + (pub.uploads_short || 0) + ' shorts') +
      tile('Net subs · ' + win, gw && gw.subscribers != null ? signed(gw.subscribers) : (lifetime ? fmt(s.subscribers) : '–'), gw && gw.subscribers != null ? 'since ' + gw.since : (lifetime ? 'total subscribers' : 'daily snapshots build this')) +
      tile('Realtime', rtVal, rtSub);
    $('#chanBody').innerHTML = `
      <div class="tiles-hero">${usePublic ? publicHero : exactHero}</div>
      <h3 class="sec-label">Details · ${win}</h3>
      <div class="tiles">${usePublic ? publicRest : exactRest}</div>
      ${usePublic ? `<p class="hint">Public-data estimates from ${fmt(pub.sample_size || 0)} scanned uploads${g.rows && g.rows.length ? ' + ' + g.rows.length + ' day' + (g.rows.length === 1 ? '' : 's') + ' of snapshots' : ''}${ch.has_token ? '' : ' · tracked by public data (no login)'}. Exact views, engaged views, watch time and revenue need OAuth on an owned channel or the extension on Studio.</p>` : ''}
      ${ch.has_token && !d.scopes.analytics ? '<p class="err">This channel is linked without the analytics scope. Settings → Link channel again (same Google account, same channel) to unlock exact totals and engaged views.</p>' : ''}
      <div class="panel"><h3>Estimated income · ${win} <span class="hint">longform $${(er.rpm_long || [2, 6])[0]}–${(er.rpm_long || [2, 6])[1]} / 1k${pub.movie_channel ? ' · movie channel' : ''} · shorts $0.20–0.40 / 1k · ${lifetime ? 'lifetime views scaled by format mix' : fmt(pub.uploads) + ' uploads in window'}</span></h3>
        <div class="tiles income">
          <div class="tile"><div class="k">Longform</div><div class="v">${rng(er.long)}</div><div class="sub">${fmt(er.long.views)} views</div></div>
          <div class="tile"><div class="k">Shorts</div><div class="v">${rng(er.short)}</div><div class="sub">${fmt(er.short.views)} views</div></div>
          <div class="tile combined"><div class="k">Combined</div><div class="v">${rng(er.combined)}</div><div class="sub">estimate</div></div>
        </div></div>
      <div class="grid2">
        <div class="panel"><h3>${usePublic ? 'Subscribers over time' : 'Views vs engaged views · daily'} <span class="hint">${usePublic ? 'daily snapshots since tracking' : 'Analytics API'}</span></h3><div class="chart" id="chChart"></div></div>
        <div class="panel"><h3>${usePublic ? 'Total views over time' : 'Subscribers'}</h3><div class="chart" id="chSubs"></div></div>
      </div>
      <div class="grid2">
        <div class="panel"><h3>Latest uploads vs typical <span class="hint">velocity vs the channel's median, same format</span></h3>
          <table><thead><tr><th>#</th><th>Video</th><th>Age</th><th>Views/day</th><th>vs typical</th></tr></thead><tbody>
          ${d.latest.map(r => `<tr><td>${r.rank}</td><td class="wrap"><img class="thumb" src="${r.thumb || ''}" alt=""><a href="https://www.youtube.com/watch?v=${r.video_id}" target="_blank">${esc(r.title)}</a> <span class="tiny">${r.is_short ? 'short' : ''}</span></td><td>${dateS(r.published_at)}</td><td>${full(r.velocity)}</td><td>${r.ratio == null ? '–' : `<span class="${r.arrow === '↑' ? 'arrow-up' : r.arrow === '↓' ? 'arrow-down' : ''}">${r.arrow} ${r.ratio}x</span>`}</td></tr>`).join('')}
          </tbody></table></div>
        <div class="panel"><h3>Top videos · ${win} <span class="hint">${usePublic ? 'by public views' : 'Analytics API'}</span></h3>
          ${usePublic
            ? `<table><thead><tr><th>Video</th><th>Views</th><th>Likes</th><th>Comments</th></tr></thead><tbody>${(pub.top_videos || []).map(r => `<tr><td class="wrap"><img class="thumb" src="${r.thumb || ''}" alt=""><a href="https://www.youtube.com/watch?v=${r.video_id}" target="_blank">${esc(r.title)}</a> <span class="tiny">${r.is_short ? 'short' : ''}</span></td><td>${full(r.views)}</td><td>${full(r.likes)}</td><td>${full(r.comments)}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No public uploads in this window.</td></tr>'}</tbody></table>`
            : `<table><thead><tr><th>Video</th><th>Views</th><th>Engaged</th><th>Hours</th></tr></thead><tbody>${d.top.slice(0, 15).map(r => `<tr><td class="wrap"><img class="thumb" src="${r.thumb || ''}" alt=""><a href="https://www.youtube.com/watch?v=${r.video_id}" target="_blank">${esc(r.title)}</a></td><td>${full(r.views_28d)}</td><td>${full(r.engaged_28d)}</td><td>${fmt((r.minutes_28d || 0) / 60)}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No analytics rows yet.</td></tr>'}</tbody></table>`}
        </div>
      </div>
      <div class="panel"><h3>Content <span class="hint">${vids.length} newest uploads · pick your columns</span></h3>
        <div class="colpick">${COLS.map(([k, n]) => `<label class="chk"><input type="checkbox" data-col="${k}" ${cols.includes(k) ? 'checked' : ''}>${n}</label>`).join('')}</div>
        <div class="row"><input id="vidFilter" placeholder="filter by title…" style="max-width:280px"><label class="chk"><input type="checkbox" id="onlyScheduled"> scheduled only</label></div>
        <div class="tablewrap"><table id="vidTable"><thead></thead><tbody></tbody></table></div>
      </div>
      <div class="grid2">
        <div class="panel"><h3>Find &amp; replace <span class="hint">bulk edit titles and descriptions (50 quota units per video)</span></h3>
          <div class="row"><input id="frFind" placeholder="find"><input id="frRepl" placeholder="replace with"></div>
          <div class="row mt"><label class="chk"><input type="checkbox" id="frTitle" checked> titles</label><label class="chk"><input type="checkbox" id="frDesc" checked> descriptions</label><label class="chk"><input type="checkbox" id="frCase"> case sensitive</label><button id="frPreview" class="ghost">Preview</button><button id="frApply" class="primary" disabled>Apply</button></div>
          <div id="frOut" class="result"></div></div>
        <div class="panel"><h3>Super Chats · last 30 days <span class="hint">Data API superChatEvents</span></h3><button id="scLoad" class="ghost">Load</button><div id="scOut" class="result"></div></div>
      </div>`;
    if (usePublic) {
      const sr = (g.rows || []).filter(x => x.subscribers != null || x.views != null);
      const snapMsg = '<p class="muted">History builds daily as CreatorHaven syncs — check back after a few days.</p>';
      if (sr.length >= 2) {
        lineChart($('#chChart'), [{ name: 'subscribers', color: cssVar('--trail'), values: sr.map(x => ({ x: x.day, y: x.subscribers })) }], { area: true });
        lineChart($('#chSubs'), [{ name: 'total views', color: '#71717a', values: sr.map(x => ({ x: x.day, y: x.views })) }], { area: true });
      } else { $('#chChart').innerHTML = snapMsg; $('#chSubs').innerHTML = snapMsg; }
    } else {
      lineChart($('#chChart'), [
        { name: 'views', color: cssVar('--trail'), values: d.series.map(x => ({ x: x.day, y: x.views })) },
        { name: 'engaged views', color: '#71717a', values: d.series.map(x => ({ x: x.day, y: x.engaged_views })) }], { area: true });
      let cSub = 0;   // Studio-style: cumulative net subscribers over the period, line + area
      lineChart($('#chSubs'), [
        { name: 'net subscribers', color: '#71717a',
          values: d.series.map(x => ({ x: x.day, y: (cSub += (x.subs_gained || 0) - (x.subs_lost || 0)) })) }], { area: true });
    }
    const renderTable = () => {
      const on = $$('.colpick input').filter(i => i.checked).map(i => i.dataset.col);
      localStorage.setItem('sf_cols', JSON.stringify(on));
      const f = ($('#vidFilter').value || '').toLowerCase();
      const onlyS = $('#onlyScheduled').checked;
      let rows = vids.filter(v => (!f || (v.title || '').toLowerCase().includes(f)) && (!onlyS || (v.privacy === 'private' && v.publish_at)));
      const sortK = state.sortK, dir = state.sortDir || -1;
      if (sortK) rows = rows.slice().sort((a, b) => ((a[sortK] ?? -Infinity) > (b[sortK] ?? -Infinity) ? 1 : -1) * dir);
      $('#vidTable thead').innerHTML = '<tr>' + on.map(k => `<th data-k="${k}">${COLS.find(c => c[0] === k)[1]}${state.sortK === k ? (dir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('') + '</tr>';
      $('#vidTable tbody').innerHTML = rows.map(v => '<tr>' + on.map(k => `<td class="${k === 'thumb' ? 'wrap' : ''}">${COLS.find(c => c[0] === k)[2](v)}</td>`).join('') + '</tr>').join('');
      $$('#vidTable th').forEach(th => th.onclick = () => { const k = th.dataset.k; state.sortDir = state.sortK === k ? -(state.sortDir || -1) : -1; state.sortK = k; renderTable(); });
    };
    $$('.colpick input').forEach(i => i.onchange = renderTable);
    $('#vidFilter').oninput = renderTable;
    $('#onlyScheduled').onchange = renderTable;
    renderTable();
    let preview = null;
    $('#frPreview').onclick = async () => {
      const body = { find: $('#frFind').value, replace: $('#frRepl').value, in_title: $('#frTitle').checked, in_description: $('#frDesc').checked, case_sensitive: $('#frCase').checked };
      const j = await api(`/api/channels/${state.channel}/find-replace`, { method: 'POST', body });
      if (j.error) { $('#frOut').innerHTML = `<p class="err">${esc(j.error)}</p>`; return; }
      preview = body;
      $('#frApply').disabled = !j.count;
      $('#frOut').innerHTML = `<p>${j.count} video(s) would change · ${j.quota_cost} quota units</p>` + j.preview.slice(0, 50).map(p => `<div class="alert"><div class="body"><b>${esc(p.title)}</b>${p.changes.title ? `<div class="m">title → ${esc(p.changes.title)}</div>` : ''}${p.changes.description ? `<div class="m">description changes</div>` : ''}</div></div>`).join('');
    };
    $('#frApply').onclick = async () => {
      if (!preview || !confirm('Apply these edits to YouTube now? This cannot be undone automatically.')) return;
      const j = await api(`/api/channels/${state.channel}/find-replace`, { method: 'POST', body: { ...preview, apply: true } });
      $('#frOut').innerHTML = j.error ? `<p class="err">${esc(j.error)}</p>` : `<p>Applied to ${j.applied.length} video(s). ${j.errors.length ? j.errors.length + ' failed.' : ''}</p>`;
      $('#frApply').disabled = true;
    };
    $('#scLoad').onclick = async () => {
      const j = await api(`/api/channels/${state.channel}/superchats`);
      $('#scOut').innerHTML = j.error ? `<p class="err">${esc(j.error)}</p>` : `<div class="tiles"><div class="tile"><div class="k">Events</div><div class="v">${j.events}</div></div><div class="tile"><div class="k">Total</div><div class="v">${full(j.total)}</div><div class="sub">${Object.entries(j.by_currency).map(([c, a]) => `${c} ${full(a)}`).join(' · ')}</div></div><div class="tile"><div class="k">Est. take-home</div><div class="v">${full(j.creator_take_home)}</div><div class="sub">${esc(j.note)}</div></div></div>` +
        (j.top.length ? `<table><thead><tr><th>When</th><th>Supporter</th><th>Amount</th><th>Message</th></tr></thead><tbody>${j.top.map(e => `<tr><td>${dateT(e.created_at)}</td><td>${esc(e.supporter)}</td><td>${esc(e.display)}</td><td class="wrap">${esc(e.comment || '')}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">No Super Chats in the last 30 days.</p>');
    };
  }

  // ------------------------------------------------------ production ledger (Monday-style pipeline)
  let ledger = null, ledgerQ = '';
  const PRIO_SYM = { high: '///', med: '//', low: '/' };
  const PRIO_CYCLE = ['high', 'med', 'low'];
  const STATUS_LIST = ['idea', 'recorded', 'editing', 'revisions', 'live'];
  const STATUS_MENU = { idea: 'Idea', recorded: 'Recorded', editing: 'Editing', revisions: 'Revisions', live: 'Live' };
  const STATUS_TOKEN = { idea: '[ STAGE.01 / IDEA ]', recorded: '[ STAGE.02 / RECORDED ]', editing: '[ STAGE.03 / ACTIVE ]', revisions: '[ STAGE.04 / REVISIONS ]', live: '[ COMPLETED ]' };
  function statusToken(r) { return STATUS_TOKEN[r.status] || STATUS_TOKEN.idea; }
  function ledgerCount() { return (ledger.groups || []).reduce((n, g) => n + (g.rows || []).length, 0); }
  async function loadPlanner() {
    const j = await api('/api/ledger');
    ledger = (j && j.ledger) || { title: 'YouTube Master Pipeline', groups: [] };
    renderLedger();
  }
  async function saveLedger() { try { await api('/api/ledger', { method: 'POST', body: { ledger } }); } catch (e) { /* keep local copy */ } }
  let lgDrag = null;                 // {g, r} being dragged between stages
  function rowHtml(g, r) {
    const q = ledgerQ.toLowerCase();
    if (q && !(((r.title || '') + ' ' + (r.asset || '') + ' ' + (r.owner || '')).toLowerCase().includes(q))) return '';
    const subs = (r.sub && r.sub.length ? r.sub : ['no sub-tasks yet']).map(s => `<div class="lg-sub">${esc(s)}</div>`).join('');
    return `<div class="lg-row" draggable="true" data-g="${esc(g.id)}" data-r="${esc(r.id)}">
        <span class="lg-asset">${esc(r.asset || '')}</span>
        <span class="lg-concept" title="${esc(r.title || '')}">${esc(r.title || '')}</span>
        <span class="lg-status ${esc(r.status || 'active')}" data-act="status" title="click to advance">${esc(statusToken(r))}</span>
        <span class="lg-prio ${esc(r.priority || 'med')}" data-act="prio" title="priority — click to change">${PRIO_SYM[r.priority] || '//'}</span>
        <span class="lg-owner">[ ${esc(r.owner || '—')} ]</span>
        <span class="lg-due ${r.due ? '' : 'empty'}" data-act="due" title="set a due date">${esc(r.due || '+ date')}</span>
        <button class="lg-del" data-act="del" title="Remove entry" aria-label="Remove entry">✕</button>
      </div><div class="lg-subwrap">${subs}</div>`;
  }
  function groupHtml(g) {
    const rows = (g.rows || []).map(r => rowHtml(g, r)).join('');
    return `<div class="lg-group" data-g="${esc(g.id)}">
      <h3 class="lg-gname">${esc(g.name || 'Group')}</h3>
      <div class="lg-gmeta">${(g.rows || []).length} entries</div>
      <div class="lg-table">
        <div class="lg-hrow"><span>Asset</span><span>Concept</span><span>Status</span><span>Prio</span><span>Owner</span><span>Due</span><span></span></div>
        ${rows || '<div class="lg-empty">No entries — drag one here.</div>'}
      </div></div>`;
  }
  function renderLedger() {
    const el = $('#ledger'); if (!el || !ledger) return;
    el.innerHTML = `
      <div class="lg-head">
        <div class="lg-id"><span class="lg-kick">Ledger</span><span class="lg-name">${esc(ledger.title || 'Master Pipeline')}</span><span class="lg-count">${ledgerCount()} assets · ${(ledger.groups || []).length} stages</span></div>
        <div class="lg-ctl"><input id="lgSearch" class="lg-search" type="search" placeholder="Filter entries…" value="${esc(ledgerQ)}"><button id="lgNew" class="lg-new">+ New entry</button></div>
      </div>
      ${(ledger.groups || []).map(groupHtml).join('') || '<p class="muted">No stages yet.</p>'}`;
    wireLedger();
  }
  function findRow(gid, rid) { const g = (ledger.groups || []).find(x => x.id === gid); return { g, r: g && (g.rows || []).find(x => x.id === rid) }; }
  function wireLedger() {
    const s = $('#lgSearch');
    if (s) s.oninput = () => { const pos = s.selectionStart; ledgerQ = s.value; renderLedger(); const s2 = $('#lgSearch'); if (s2) { s2.focus(); try { s2.setSelectionRange(pos, pos); } catch (e) { } } };
    const nb = $('#lgNew'); if (nb) nb.onclick = addLedgerEntry;
    $$('#ledger .lg-row').forEach(row => {
      row.onclick = e => {
        const act = e.target.closest('[data-act]');
        const { g, r } = findRow(row.dataset.g, row.dataset.r);
        if (!g || !r) return;
        if (act) {
          e.stopPropagation();
          const a = act.dataset.act;
          if (a === 'status') { openStatusMenu(act, r); }
          else if (a === 'prio') { r.priority = PRIO_CYCLE[(PRIO_CYCLE.indexOf(r.priority) + 1) % 3]; saveLedger(); renderLedger(); }
          else if (a === 'del') { g.rows = g.rows.filter(x => x.id !== r.id); saveLedger(); renderLedger(); }
          else if (a === 'due') { openDueCalendar(act, r); }
          return;
        }
        row.classList.toggle('exp');
      };
      row.ondragstart = e => { lgDrag = { g: row.dataset.g, r: row.dataset.r }; row.classList.add('lg-dragging'); try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', row.dataset.r); } catch (_) { } };
      row.ondragend = () => { lgDrag = null; $$('#ledger .lg-dragging').forEach(x => x.classList.remove('lg-dragging')); $$('#ledger .lg-drop').forEach(x => x.classList.remove('lg-drop')); };
    });
    $$('#ledger .lg-group').forEach(grp => {
      grp.ondragover = e => { if (!lgDrag) return; e.preventDefault(); try { e.dataTransfer.dropEffect = 'move'; } catch (_) { } grp.classList.add('lg-drop'); };
      grp.ondragleave = e => { if (!grp.contains(e.relatedTarget)) grp.classList.remove('lg-drop'); };
      grp.ondrop = e => {
        e.preventDefault(); grp.classList.remove('lg-drop');
        if (!lgDrag) return;
        const { g: sg, r } = findRow(lgDrag.g, lgDrag.r);
        const dg = (ledger.groups || []).find(x => x.id === grp.dataset.g);
        lgDrag = null;
        if (!sg || !r || !dg || sg.id === dg.id) return;   // dropped in its own stage → no change
        sg.rows = (sg.rows || []).filter(x => x.id !== r.id);
        (dg.rows = dg.rows || []).push(r);
        saveLedger(); renderLedger();
      };
    });
  }
  function openDueCalendar(anchor, r) {
    sfCalendar(anchor, r.due).then(val => {
      if (val === undefined) return;      // dismissed — leave the date as-is
      r.due = val || '';                  // '' = cleared
      saveLedger(); renderLedger();
    });
  }
  function openStatusMenu(anchor, r) {
    sfMenu(anchor, STATUS_LIST.map(s => ({ value: s, label: STATUS_MENU[s], cls: s })), r.status).then(v => {
      if (v === undefined) return;
      r.status = v; saveLedger(); renderLedger();
    });
  }
  // small anchored picker — resolves the chosen value, or undefined if dismissed
  function sfMenu(anchor, options, current) {
    return new Promise(resolve => {
      const back = document.createElement('div'); back.className = 'sf-cal-back';
      const menu = document.createElement('div'); menu.className = 'sf-menu';
      menu.innerHTML = options.map(o => `<button type="button" class="sf-menu-item lgstat lgstat-${esc(o.cls || '')}${o.value === current ? ' on' : ''}" data-v="${esc(o.value)}"><span class="sf-menu-dot"></span>${esc(o.label)}</button>`).join('');
      back.appendChild(menu); document.body.appendChild(back);
      let settled = false;
      const finish = v => { if (settled) return; settled = true; document.removeEventListener('keydown', onKey, true); back.remove(); resolve(v); };
      const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); finish(undefined); } };
      const rect = anchor.getBoundingClientRect();
      menu.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 190)) + 'px';
      menu.style.top = Math.min(rect.bottom + 6, window.innerHeight - 230) + 'px';
      menu.querySelectorAll('[data-v]').forEach(b => b.onclick = () => finish(b.dataset.v));
      back.addEventListener('mousedown', e => { if (e.target === back) finish(undefined); });
      document.addEventListener('keydown', onKey, true);
    });
  }
  async function addLedgerEntry() {
    const title = await sfPrompt({ heading: 'New production entry', kick: 'Production ledger', label: 'Video concept title', placeholder: 'e.g. I Survived 100 Hours in the Backrooms', confirm: 'Add entry' });
    if (!title) return;
    const g = (ledger.groups || [])[0]; if (!g) return;
    const due = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10);
    (g.rows = g.rows || []).unshift({ id: 'r' + Date.now().toString(36), asset: 'YT-' + String(100 + ledgerCount() + 1), title, stage: 'STAGE.01', status: 'idea', priority: 'med', owner: 'E.M', due, sub: [] });
    await saveLedger(); renderLedger();
  }
  // clean obsidian dropdown calendar — resolves 'YYYY-MM-DD', '' (cleared) or undefined (dismissed)
  function sfCalendar(anchor, current) {
    return new Promise(resolve => {
      const back = document.createElement('div'); back.className = 'sf-cal-back';
      const cal = document.createElement('div'); cal.className = 'sf-cal';
      back.appendChild(cal); document.body.appendChild(back);
      const pad = n => String(n).padStart(2, '0');
      const parse = str => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(str || ''); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null; };
      const iso = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
      const sel = parse(current), todayIso = iso(new Date());
      let view = sel ? new Date(sel.getFullYear(), sel.getMonth(), 1) : new Date(new Date().getFullYear(), new Date().getMonth(), 1);
      let settled = false;
      const finish = v => { if (settled) return; settled = true; document.removeEventListener('keydown', onKey, true); back.remove(); resolve(v); };
      const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); finish(undefined); } };
      function draw() {
        const y = view.getFullYear(), mo = view.getMonth();
        const first = new Date(y, mo, 1).getDay(), days = new Date(y, mo + 1, 0).getDate();
        let cells = '';
        for (let i = 0; i < first; i++) cells += '<span class="sf-cal-pad"></span>';
        for (let d = 1; d <= days; d++) { const ds = y + '-' + pad(mo + 1) + '-' + pad(d); const c = ['sf-cal-day']; if (ds === todayIso) c.push('today'); if (sel && ds === iso(sel)) c.push('sel'); cells += `<button type="button" class="${c.join(' ')}" data-d="${ds}">${d}</button>`; }
        cal.innerHTML = `<div class="sf-cal-head"><button type="button" class="sf-cal-nav" data-nav="-1">‹</button><b>${view.toLocaleString(undefined, { month: 'long', year: 'numeric' })}</b><button type="button" class="sf-cal-nav" data-nav="1">›</button></div>
          <div class="sf-cal-grid sf-cal-dow"><span>Su</span><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span></div>
          <div class="sf-cal-grid sf-cal-days">${cells}</div>
          <div class="sf-cal-foot"><button type="button" class="sf-cal-clear">Clear</button><button type="button" class="sf-cal-today">Today</button></div>`;
        cal.querySelectorAll('[data-nav]').forEach(b => b.onclick = () => { view.setMonth(view.getMonth() + (+b.dataset.nav)); draw(); });
        cal.querySelectorAll('[data-d]').forEach(b => b.onclick = () => finish(b.dataset.d));
        cal.querySelector('.sf-cal-clear').onclick = () => finish('');
        cal.querySelector('.sf-cal-today').onclick = () => finish(todayIso);
      }
      const rect = anchor.getBoundingClientRect();
      cal.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 268)) + 'px';
      cal.style.top = Math.min(rect.bottom + 6, window.innerHeight - 316) + 'px';
      back.addEventListener('mousedown', e => { if (e.target === back) finish(undefined); });
      document.addEventListener('keydown', onKey, true);
      draw();
    });
  }
  // bespoke in-app modal (replaces window.prompt) — obsidian canvas, gold accents, spring slide-in
  function sfPrompt({ dateStr, heading, kick = 'Content planner', label = 'Video title / idea', placeholder = 'e.g. I survived 24h in the wilderness', confirm = 'Schedule' }) {
    return new Promise(resolve => {
      const back = document.createElement('div');
      back.className = 'sf-modal-back';
      back.innerHTML = `
        <div class="sf-modal" role="dialog" aria-modal="true" aria-label="${esc(heading || 'Plan an entry')}">
          <div class="sf-modal-kick">${esc(kick)}</div>
          <h3 class="sf-modal-title">${esc(heading || ('Plan an entry for ' + (dateStr || '')))}</h3>
          <label class="sf-modal-label" for="sfModalInput">${esc(label)}</label>
          <input id="sfModalInput" class="sf-modal-input" type="text" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false">
          <div class="sf-modal-actions">
            <button type="button" class="sf-modal-cancel">Cancel</button>
            <button type="button" class="sf-modal-confirm">${esc(confirm)}</button>
          </div>
        </div>`;
      document.body.appendChild(back);
      const input = back.querySelector('#sfModalInput');
      const close = val => { back.classList.remove('show'); document.removeEventListener('keydown', onKey); setTimeout(() => back.remove(), 220); resolve(val); };
      const onKey = e => { if (e.key === 'Escape') close(null); else if (e.key === 'Enter' && document.activeElement === input) { e.preventDefault(); close(input.value.trim() || null); } };
      back.querySelector('.sf-modal-cancel').onclick = () => close(null);
      back.querySelector('.sf-modal-confirm').onclick = () => close(input.value.trim() || null);
      back.addEventListener('mousedown', e => { if (e.target === back) close(null); });
      document.addEventListener('keydown', onKey);
      void back.offsetWidth;                 // force reflow so the enter transition plays (robust even in a background tab)
      back.classList.add('show');
      input.focus();
    });
  }

  // -------------------------------------------------------------- alerts
  const alertHtml = a => `<div class="alert ${a.dismissed ? 'dismissed' : ''}"><div class="sev ${a.severity}"></div><div class="body"><div>${a.link ? `<a href="${a.link}" target="_blank">${esc(a.title)}</a>` : esc(a.title)}</div><div class="m">${esc(a.message || '')} · ${ago(a.created_at)}</div></div>${a.dismissed ? '' : `<button class="small" data-dismiss="${a.id}">dismiss</button>`}</div>`;
  const bindAlertButtons = el => $$('[data-dismiss]', el).forEach(b => b.onclick = async () => { await api(`/api/alerts/${b.dataset.dismiss}/dismiss`, { method: 'POST', body: {} }); b.closest('.alert').remove(); refreshStatus(); });
  async function loadAlerts() {
    const j = await api('/api/alerts' + ($('#alertsAll').checked ? '?all=1' : ''));
    $('#alertsList').innerHTML = (j.alerts || []).map(alertHtml).join('') || '<p class="muted">No alerts yet — you\'ll be alerted here when your tracked channels post, hit an outlier, cross a milestone, or a planned post is due. Reminders can also go to Discord (Settings).</p>';
    bindAlertButtons($('#alertsList'));
  }
  $('#alertsAll').onchange = loadAlerts;
  $('#alertsDeliver').onclick = async () => { const j = await api('/api/alerts/deliver', { method: 'POST', body: {} }); toast(j.delivered ? `Sent ${j.delivered} alert(s) to Discord` : (j.error || 'Nothing new to send (or no webhook set)')); };

  // --------------------------------------------------------------- tools
  const TOOLS = [
    ['Analyze', 'video-analyzer', 'Video analyzer', 'Full read on any video: views & velocity, an estimated retention (AVD) curve, estimated CTR, engagement, engaged views, monetization signals and tags.', [['video', 'Video URL or id']]],
    ['Analyze', 'playlist-analyzer', 'Playlist analyzer', 'Total views, duration and top videos for any playlist.', [['playlist', 'Playlist URL or id']]],
    ['Analyze', 'keyword', 'Keyword analyzer', 'Who ranks for a search term, how big they are, how fresh, and a difficulty score.', [['keyword', 'Keyword'], ['region', 'Region code (optional, e.g. US)']]],
    ['Check', 'tag-rank', 'Tag rank checker', 'Where a video ranks for each of its own tags (100 quota units per tag).', [['video', 'Video URL or id'], ['max_tags', 'Max tags to check (default 15)']]],
    ['Check', 'ad-safety', 'Ad-safety checker', 'Gemini reviews a title, description or script for advertiser-unfriendly content.', [['text', 'Title / description / script', 'textarea']]],
    ['Download', 'thumbnails', 'Thumbnail downloader', 'Every thumbnail size for a video, with a check of which really exist.', [['video', 'Video URL or id']]],
    ['Download', 'channel-images', 'Profile picture & banner', 'High-resolution avatar and banner URLs for any channel.', [['channel', 'Channel URL or @handle']]],
    ['Download', 'comment-export', 'Comment exporter', 'Export a video\'s comments to CSV/JSON.', [['video', 'Video URL or id'], ['limit', 'Max comments (default 1000)']]],
    ['Download', 'playlist-export', 'Playlist exporter', 'A playlist as a spreadsheet with per-video stats.', [['playlist', 'Playlist URL or id']]],
    ['Download', 'channel-backup', 'Channel backup', 'Full snapshot of a channel\'s catalogue (titles, descriptions, tags, stats) as CSV + JSON.', [['channel', 'Channel URL or @handle (your own channels give private fields too)']]],
    ['Create', 'tags', 'Tag generator', 'Gemini writes ranked, paste-ready tags under the 500-character limit.', [['title', 'Video title'], ['description', 'Description (optional)', 'textarea'], ['niche', 'Niche / context']]],
    ['Create', 'thumbnail-analyzer', 'Thumbnail analyzer', 'Pixel metrics plus a Gemini vision score for clarity, contrast, colour and text.', [['image', 'Thumbnail image', 'file'], ['title', 'Video title']]],
    ['Create', 'comment-picker', 'Comment picker', 'Fair giveaway winners from a video\'s comments (seeded, reproducible).', [['video', 'Video URL or id'], ['winners', 'Winners (default 1)'], ['keyword', 'Must contain (optional)']]],
    ['Create', 'subscribe-link', 'Subscribe link maker', 'A link that opens the channel with the subscribe prompt.', [['channel', 'Channel URL or @handle']]],
  ];
  const TOOLCAT = {
    Analyze: { hue: 'var(--violet)', blurb: 'Understand any video, playlist or keyword', d: 'M4 20V10M10 20V4M16 20v-7M20 20H2' },
    Check: { hue: 'var(--cyan)', blurb: 'Rankings, sponsors, ad-safety and IDs', d: 'M12 3l7 3v5c0 4.2-3 7.4-7 8.4C8 18.4 5 15.2 5 11V6zM9 11.5l2 2 4-4' },
    Download: { hue: 'var(--emerald)', blurb: 'Pull thumbnails, images, comments, backups', d: 'M12 3v11M7.5 10.5L12 15l4.5-4.5M4 20h16' },
    Create: { hue: 'var(--amber)', blurb: 'Tags, thumbnails, giveaways and links', d: 'M12 3l1.8 4.7L18 9l-4.2 1.3L12 15l-1.8-4.7L6 9l4.2-1.3z' },
  };
  const toolIcon = cat => { const c = TOOLCAT[cat] || {}; return `<span class="tool-ic" style="background:${c.hue || 'var(--accent)'}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${c.d || ''}"/></svg></span>`; };
  function loadTools() {
    const groups = [...new Set(TOOLS.map(t => t[0]))];
    // left rail: collapsible category dropdowns
    $('#toolList').innerHTML = `<button class="tl-home ${state.tool ? '' : 'on'}" data-home>All tools</button>` + groups.map(g =>
      `<details class="toolcat" open><summary>${g}<span class="tc-count">${TOOLS.filter(t => t[0] === g).length}</span></summary>` +
      TOOLS.filter(t => t[0] === g).map(t => `<button data-tool="${t[1]}" class="${state.tool === t[1] ? 'on' : ''}">${t[2]}</button>`).join('') + `</details>`).join('');
    $('#toolList [data-home]').onclick = () => { state.tool = null; loadTools(); };
    $$('#toolList button[data-tool]').forEach(b => b.onclick = () => { state.tool = b.dataset.tool; loadTools(); renderTool(); });
    state.tool ? renderTool() : renderToolLanding(groups);
  }
  function renderToolLanding(groups) {
    $('#toolPane').innerHTML = `
      <div class="tool-landing">
        <h2 class="tl-title">Your creator toolkit</h2>
        <p class="desc">${TOOLS.length} focused utilities — research a video, check where you rank, pull assets, or spin up tags and thumbnails. Pick one to begin.</p>
        ${groups.map(g => `<section class="tool-group"><h3>${g} <span class="hint">${TOOLCAT[g] ? TOOLCAT[g].blurb : ''}</span></h3>
          <div class="tool-cards">${TOOLS.filter(t => t[0] === g).map((t, i) => `<button class="tool-card" data-tool="${t[1]}" style="animation-delay:${i * 0.03}s">${toolIcon(g)}<span class="tc-body"><b>${t[2]}</b><span>${t[3]}</span></span></button>`).join('')}</div></section>`).join('')}
      </div>`;
    $$('#toolPane .tool-card').forEach(b => b.onclick = () => { state.tool = b.dataset.tool; loadTools(); renderTool(); });
  }
  function renderTool() {
    const t = TOOLS.find(x => x[1] === state.tool);
    $('#toolPane').innerHTML = `<button class="ghost small tool-back" id="toolBack">← All tools</button>
      <div class="tool-head">${toolIcon(t[0])}<div><h2 class="tl-title">${t[2]}</h2><p class="desc">${t[3]}</p></div></div>` +
      t[4].map(([k, label, kind]) => `<label>${label}${kind === 'textarea' ? `<textarea data-f="${k}"></textarea>` : kind === 'file' ? `<input type="file" data-f="${k}" accept="image/*">` : `<input data-f="${k}">`}</label>`).join('') +
      `<div class="row mt"><button id="toolRun" class="primary">Run</button><span id="toolMsg" class="msg"></span></div><div id="toolOut" class="result"></div>`;
    $('#toolBack').onclick = () => { state.tool = null; loadTools(); };
    $('#toolRun').onclick = runTool;
    $$('#toolPane input[data-f]:not([type=file])').forEach(i => i.onkeydown = e => { if (e.key === 'Enter') runTool(); });
  }
  async function runTool() {
    const t = TOOLS.find(x => x[1] === state.tool);
    const body = {};
    for (const [k, , kind] of t[4]) {
      const el = $(`#toolPane [data-f="${k}"]`);
      if (kind === 'file') {
        const f = el.files?.[0];
        if (!f) { $('#toolMsg').textContent = 'Pick an image.'; return; }
        body.image_b64 = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result.split(',')[1]); fr.readAsDataURL(f); });
        body.mime = f.type || 'image/jpeg';
      } else body[k] = el.value.trim();
    }
    $('#toolMsg').textContent = 'Running…'; $('#toolOut').innerHTML = '';
    const j = await api(`/api/tools/${state.tool}`, { method: 'POST', body });
    $('#toolMsg').textContent = '';
    if (!j.ok) { $('#toolOut').innerHTML = `<p class="err">${esc(j.error || 'failed')}</p>`; return; }
    $('#toolOut').innerHTML = renderResult(state.tool, j.result);
    $$('#toolOut [data-copy]').forEach(b => b.onclick = () => { navigator.clipboard.writeText(b.dataset.copy); toast('Copied'); });
    $$('#toolOut [data-dl]').forEach(b => b.onclick = () => { const blob = new Blob([b.dataset.dl], { type: 'text/csv' }); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = b.dataset.name || 'export.csv'; a.click(); });
  }
  const pre = o => `<pre>${esc(JSON.stringify(o, null, 2))}</pre>`;
  const csvBtn = (csv, name) => `<button class="ghost" data-dl="${esc(csv)}" data-name="${name}">Download CSV</button>`;
  // estimated audience-retention curve (x = % through the video, y = % still watching)
  function retChart(ret) {
    const curve = ret.curve || []; if (!curve.length) return '';
    const W = 680, H = 200, P = { l: 32, r: 14, t: 12, b: 24 };
    const X = p => P.l + (p / 100) * (W - P.l - P.r);
    const Y = v => H - P.b - (v / 100) * (H - P.t - P.b);
    const line = curve.map(c => `${X(c.pct)},${Y(c.retention)}`).join(' ');
    const gold = cssVar('--trail');
    let g = '';
    for (let i = 0; i <= 4; i++) { const yv = i * 25; g += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${Y(yv)}" y2="${Y(yv)}" stroke="${cssVar('--line')}"/><text x="${P.l - 5}" y="${Y(yv) + 3}" fill="${cssVar('--muted')}" font-size="9" text-anchor="end">${yv}%</text>`; }
    [0, 25, 50, 75, 100].forEach(p => g += `<text x="${X(p)}" y="${H - 7}" fill="${cssVar('--muted')}" font-size="9" text-anchor="middle">${p}%</text>`);
    if (ret.avd_pct != null) { const ay = Y(ret.avd_pct); g += `<line x1="${P.l}" x2="${W - P.r}" y1="${ay}" y2="${ay}" stroke="${gold}" stroke-dasharray="2 3" opacity=".5"/><text x="${W - P.r}" y="${ay - 4}" fill="${gold}" font-size="9" text-anchor="end">AVD ${ret.avd_pct}%</text>`; }
        g += `<polyline class="series anim" pathLength="1" fill="none" stroke="${gold}" stroke-width="1.5" stroke-linejoin="round" points="${line}"/>`;
    return `<div class="chart" style="height:200px"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}</svg></div>`;
  }
  function renderResult(tool, r) {
    switch (tool) {
      case 'thumbnails': return `<div class="imgrow">${r.sizes.map(s => `<a href="${s.url}" target="_blank" title="${s.name} ${s.width}×${s.height}${s.exists === false ? ' (missing)' : ''}"><img src="${s.url}" alt="" style="${s.exists === false ? 'opacity:.3' : ''}"><div class="tiny">${s.name} ${s.width}×${s.height} ${s.exists === false ? '· missing' : s.bytes ? '· ' + Math.round(s.bytes / 1024) + ' KB' : ''}</div></a>`).join('')}</div>`;
      case 'channel-images': return `<p><b>${esc(r.channel.title)}</b> · ${fmt(r.channel.subscribers)} subs</p><div class="imgrow"><a href="${r.profile.max}" target="_blank"><img src="${r.profile.large}" alt=""><div class="tiny">profile (click for 2048px)</div></a>${r.banner.base ? `<a href="${r.banner.desktop}" target="_blank"><img src="${r.banner.desktop}" alt=""><div class="tiny">banner desktop 2560px</div></a><a href="${r.banner.tv}" target="_blank"><div class="tiny">banner TV 2120px (full art)</div></a>` : '<p class="muted">No banner.</p>'}</div>`;
      case 'channel-id': return `<div class="kv"><div><span>Channel</span><span>${esc(r.title)}</span></div><div><span>ID</span><span>${r.channel_id} <button class="small" data-copy="${r.channel_id}">copy</button></span></div><div><span>Handle</span><span>${esc(r.handle || '–')}</span></div><div><span>Subscribers</span><span>${full(r.subscribers)}</span></div><div><span>Views</span><span>${full(r.views)}</span></div><div><span>Videos</span><span>${full(r.videos)}</span></div><div><span>Country</span><span>${esc(r.country || '–')}</span></div><div><span>Created</span><span>${dateS(r.published_at)}</span></div><div><span>RSS</span><span><a href="${r.rss}" target="_blank">feed</a></span></div></div>`;
      case 'comment-export': return `<p>${r.count} comments · ${csvBtn(r.csv, `comments-${r.video_id}.csv`)}</p><table><thead><tr><th>Author</th><th>Likes</th><th>Comment</th></tr></thead><tbody>${r.rows.slice(0, 100).map(c => `<tr><td>${esc(c.author)}</td><td>${c.likes}</td><td class="wrap">${esc(c.text)}</td></tr>`).join('')}</tbody></table>`;
      case 'playlist-export': case 'playlist-analyzer': return `<p><b>${esc(r.playlist?.title)}</b> · ${r.count} videos · ${fmt(r.total_views)} total views · ${dur(r.total_duration_s)} total · ${csvBtn(r.csv, 'playlist.csv')}</p><h3>Top by views</h3><table><thead><tr><th>#</th><th>Title</th><th>Views</th><th>Length</th></tr></thead><tbody>${r.top.map(v => `<tr><td>${v.position + 1}</td><td class="wrap"><a href="${v.url}" target="_blank">${esc(v.title)}</a></td><td>${full(v.views)}</td><td>${dur(v.duration_s)}</td></tr>`).join('')}</tbody></table>`;
      case 'channel-backup': return `<p><b>${esc(r.channel.title)}</b> · ${r.count} videos · ${csvBtn(r.csv, 'channel-backup.csv')} <button class="ghost" data-dl="${esc(JSON.stringify(r.json))}" data-name="channel-backup.json">Download JSON</button></p>`;
      case 'video-analyzer': {
        const v = r.video, s = r.signals || {}, ret = r.retention || {}, ctr = r.ctr_est || {};
        const pct = x => x == null ? '–' : (x * 100).toFixed(2) + '%';
        return `<p><b>${esc(v.title)}</b> · ${esc(v.channel_title)} · ${dateS(v.published_at)} · ${dur(v.duration_s)} · ${esc(r.format || (v.is_short ? 'Short' : 'Long-form'))}${v.made_for_kids ? ' · MADE FOR KIDS' : ''}${v.yt_rating ? ' · AGE RESTRICTED' : ''}</p>
          <div class="tiles">
            <div class="tile"><div class="k">Views</div><div class="v">${fmt(v.views)}</div><div class="sub">${full(r.views_per_day)}/day · ${r.age_days}d old</div></div>
            <div class="tile"><div class="k">Est. AVD</div><div class="v">${ret.avd_pct != null ? ret.avd_pct + '%' : '–'}</div><div class="sub">~${dur(ret.avd_seconds || 0)} watched · estimated</div></div>
            <div class="tile"><div class="k">Est. CTR</div><div class="v">${ctr.low != null ? ctr.low + '–' + ctr.high + '%' : '–'}</div><div class="sub">estimated, not measured</div></div>
            <div class="tile"><div class="k">Hook kept</div><div class="v">${ret.hook_pct != null ? ret.hook_pct + '%' : '–'}</div><div class="sub">past the intro · est.</div></div>
            <div class="tile"><div class="k">Engagement</div><div class="v">${pct(r.engagement_rate)}</div><div class="sub">likes + comments / views</div></div>
            <div class="tile"><div class="k">Engaged views</div><div class="v">${r.engaged_views_est != null ? fmt(r.engaged_views_est) : '–'}</div><div class="sub">~${Math.round((r.engaged_ratio_est || 0) * 100)}% · estimated</div></div>
            <div class="tile"><div class="k">Like rate</div><div class="v">${pct(r.like_rate)}</div><div class="sub">${full(v.likes)} likes</div></div>
            <div class="tile"><div class="k">Comment rate</div><div class="v">${pct(r.comment_rate)}</div><div class="sub">${full(v.comments)} comments</div></div>
            <div class="tile"><div class="k">Monetized?</div><div class="v">${r.signals ? (s.monetized ? 'yes' : 'no ads seen') : '?'}</div><div class="sub">${r.signals ? `${s.ad_slots} ad slots · ${esc(s.category || '')}` : esc(r.signals_error || '')}</div></div>
          </div>
          ${ret.curve ? `<div class="panel"><h3>Estimated audience retention <span class="hint">modelled from public signals — link the channel for real AVD</span></h3>${retChart(ret)}<p class="tiny">A sharp hook drop in the first ~12%, then a smooth decay. Higher engagement holds viewers longer; longer videos shed more.</p></div>` : ''}
          <p class="tiny">${esc(r.estimated_note || '')}</p>
          <p><b>Tags (${(v.tags || []).length}):</b> ${(v.tags || []).map(t => `<span class="pill">${esc(t)}</span>`).join(' ') || '<span class="muted">none</span>'}</p>`;
      }
      case 'monetization': return `<div class="tiles"><div class="tile"><div class="k">Monetized signal</div><div class="v">${r.monetized ? 'YES' : 'NO'}</div><div class="sub">${r.ad_slots} ad placements · ${r.player_ads} player ads</div></div><div class="tile"><div class="k">Playable</div><div class="v">${r.playable ? 'yes' : 'no'}</div><div class="sub">${esc(r.playability || '')}</div></div><div class="tile"><div class="k">Family safe</div><div class="v">${r.family_safe == null ? '–' : r.family_safe ? 'yes' : 'no'}</div><div class="sub">${r.countries} countries</div></div></div><p><b>${esc(r.title)}</b> · ${esc(r.channel)} · ${esc(r.category || '')}</p><p class="tiny">"Monetized" means the public player response carried ad placements at the time of the check. Ads can be absent for a monetized video (viewer region, ad inventory) and present on claimed videos (ads for the claimant), so treat this as a signal, not proof.</p>`;
      case 'keyword': return `<div class="tiles"><div class="tile"><div class="k">Difficulty</div><div class="v">${r.difficulty}</div><div class="sub">${esc(r.verdict)}</div></div><div class="tile"><div class="k">Top-10 median subs</div><div class="v">${fmt(r.median_subscribers_top10)}</div></div><div class="tile"><div class="k">Small channels in top 50</div><div class="v">${r.small_channels_in_top50}</div><div class="sub">under 10K subs</div></div><div class="tile"><div class="k">Fresh (30d) in top 50</div><div class="v">${r.fresh_last30d}</div></div></div><p><b>Common title words:</b> ${r.titles_words.map(([w, n]) => `<span class="pill">${esc(w)} ${n}</span>`).join(' ')}</p><table><thead><tr><th>#</th><th>Title</th><th>Channel</th><th>Subs</th><th>Views</th><th>Age</th></tr></thead><tbody>${r.results.map(x => `<tr><td>${x.rank}</td><td class="wrap"><a href="https://www.youtube.com/watch?v=${x.video_id}" target="_blank">${esc(x.title)}</a></td><td>${esc(x.channel)}</td><td>${fmt(x.subscribers)}</td><td>${fmt(x.views)}</td><td>${x.age_days}d</td></tr>`).join('')}</tbody></table>`;
      case 'rank': return `<div class="tile"><div class="k">Rank for “${esc(r.keyword)}”</div><div class="v">${r.rank ? '#' + r.rank : 'not in top ' + r.checked}</div><div class="sub">${r.kind} ${r.target}</div></div><h3 class="mt">Top 10 right now</h3><ol>${r.top.map(x => `<li><a href="https://www.youtube.com/watch?v=${x.video_id}" target="_blank">${esc(x.title)}</a> <span class="tiny">${esc(x.channel_title)}</span></li>`).join('')}</ol>`;
      case 'tag-rank': return `<p><b>${esc(r.title)}</b> · ${r.tags_checked} of ${r.tags_total} tags checked · <span class="tiny">${r.quota_note}</span></p><table><thead><tr><th>Tag</th><th>Rank (top 50)</th></tr></thead><tbody>${r.ranks.map(x => `<tr><td>${esc(x.tag)}</td><td>${x.rank ? `<span class="pill good">#${x.rank}</span>` : '<span class="muted">not ranking</span>'}</td></tr>`).join('')}</tbody></table>`;
      case 'sponsors': return r.segments.length ? `<table><thead><tr><th>Category</th><th>Start</th><th>End</th><th>Length</th><th>Votes</th></tr></thead><tbody>${r.segments.map(s => `<tr><td><span class="pill">${s.category}</span></td><td><a href="${s.link}" target="_blank">${dur(Math.floor(s.start))}</a></td><td>${dur(Math.floor(s.end))}</td><td>${s.duration}s</td><td>${s.votes}</td></tr>`).join('')}</tbody></table><p class="tiny">${esc(r.source)}</p>` : `<p class="muted">${esc(r.note || 'No segments.')}</p>`;
      case 'comment-picker': return `<p>${r.eligible} eligible · seed ${r.seed}</p>${r.winners.map(w => `<div class="alert"><div class="sev good"></div><div class="body"><b>${esc(w.author)}</b><div class="m">${esc(w.text)}</div></div></div>`).join('')}`;
      case 'subscribe-link': return `<p><a href="${r.link}" target="_blank">${r.link}</a> <button class="small" data-copy="${r.link}">copy</button></p>`;
      case 'tags': return `<p><b>${r.tags.length} tags · ${r.chars} chars</b> <button class="small" data-copy="${esc(r.paste)}">copy all</button></p><p>${r.tags.map(t => `<span class="pill">${esc(t)}</span>`).join(' ')}</p><p><b>Title keywords:</b> ${(r.title_keywords || []).map(t => `<span class="pill">${esc(t)}</span>`).join(' ')}</p><p class="muted">${esc(r.note || '')}</p>`;
      case 'ad-safety': return `<div class="tiles"><div class="tile"><div class="k">Risk</div><div class="v">${esc(r.risk || '?')}</div><div class="sub">score ${r.score ?? '–'}/100</div></div></div><p>${esc(r.summary || '')}</p>${(r.flags || []).map(f => `<div class="alert"><div class="sev warn"></div><div class="body"><b>${esc(f.quote)}</b><div class="m">${esc(f.issue)} → ${esc(f.fix)}</div></div></div>`).join('')}${r.safer_title ? `<p><b>Safer title:</b> ${esc(r.safer_title)}</p>` : ''}`;
      case 'thumbnail-analyzer': { const m = r.metrics, a = r.ai || {}; return `<div class="tiles"><div class="tile"><div class="k">Overall</div><div class="v">${a.overall ?? '–'}</div><div class="sub">Gemini</div></div><div class="tile"><div class="k">Clarity</div><div class="v">${a.clarity ?? '–'}</div></div><div class="tile"><div class="k">Contrast</div><div class="v">${a.contrast ?? '–'}</div><div class="sub">measured ${m.contrast}</div></div><div class="tile"><div class="k">Color</div><div class="v">${a.color ?? '–'}</div><div class="sub">saturation ${m.saturation}</div></div><div class="tile"><div class="k">Text</div><div class="v">${a.text_readability ?? '–'}</div></div><div class="tile"><div class="k">Emotion</div><div class="v">${a.emotion ?? '–'}</div></div></div><p>${m.width}×${m.height} ${m.is_16_9 ? '(16:9 ✓)' : '<span class="warn">(not 16:9)</span>'} · brightness ${m.brightness} · dominant ${m.dominant.map(c => `<span class="pill" style="border-color:${c}">${c}</span>`).join(' ')}</p>${a.error ? `<p class="err">${esc(a.error)}</p>` : `<p><b>At 160px:</b> ${esc(a.small_size_read || '')}</p><p><b>Works:</b> ${(a.what_works || []).map(esc).join(' · ')}</p><p><b>Fix first:</b> ${(a.fix_first || []).map(esc).join(' · ')}</p><p><b>Text seen:</b> ${esc(a.text_detected || '')}</p>`}`; }
      default: return pre(r);
    }
  }

  // ------------------------------------------------------------ settings
  async function loadSettings() {
    const s = await refreshStatus();
    $('#statusBox').innerHTML = [['Google OAuth client', s.google_client ? `configured (${s.google_client_id_tail}, ${s.google_client_type})` : 'MISSING'], ['Gemini key', s.gemini ? 'present' : 'missing'], ['Storage', s.persistence && s.persistence.enabled ? `durable (Postgres backup${s.persistence.pushes ? ', ' + s.persistence.pushes + ' pushes' : ''}${s.persistence.error ? ' · ' + s.persistence.error : ''})` : (s.hosted ? 'EPHEMERAL — add a DATABASE_URL or data resets on every deploy' : 'local SQLite file')], ['YouTube API key', s.youtube_api_key ? 'present' : 'not set (OAuth tokens used)'], ['Discord webhook', s.discord ? 'set' : 'not set'], ['Linked channels', s.channels], ['Quota today', `${full(s.quota_today?.units)} units / ${s.quota_today?.calls} calls`], ['Last sync', ago(s.last_sync?.at)], ['Base URL', s.base_url]].map(([k, v]) => `<div><span>${k}</span><span>${esc(v)}</span></div>`).join('');
    $('#setSync').value = s.sync_minutes; $('#setScan').value = s.scan_per_channel; $('#setDays').value = s.analytics_days; $('#setBase').value = s.base_url; $('#setGtype').value = s.google_client_type;
    $('#extUrl').textContent = s.base_url; $('#extToken').textContent = s.ext_token;
    const l = await api('/api/channels');
    $('#linkedList').innerHTML = (l.channels || []).map(c => `<div class="row between"><span>${c.thumb ? `<img src="${c.thumb}" style="width:20px;height:20px;border-radius:50%;vertical-align:middle"> ` : ''}${esc(c.title)} <span class="tiny">${(c.scopes || '').includes('yt-analytics') ? 'analytics ✓' : '<span class="warn">no analytics scope</span>'}${(c.scopes || '').includes('force-ssl') ? ' · edit ✓' : ''}</span></span><button class="small" data-unlink="${c.channel_id}">remove</button></div>`).join('') || '<p class="muted">None yet.</p>';
    $$('[data-unlink]').forEach(b => b.onclick = async () => { if (!confirm('Remove this channel from CreatorHaven? (The schedule bot keeps its own link.)')) return; await api(`/api/channels/${b.dataset.unlink}`, { method: 'DELETE' }); loadSettings(); });
    const lg = await api('/api/log'); $('#logBox').textContent = (lg.lines || []).join('\n');
  }
  $('#linkBtn').onclick = async () => {
    const sets = ['readonly', ...$$('.scopes input:checked').map(i => i.dataset.scope).filter(x => x !== 'readonly')];
    const j = await api(`/api/oauth/start?sets=${sets.join(',')}`);
    if (j.error) { $('#linkMsg').textContent = j.error; return; }
    $('#linkMsg').innerHTML = j.paste_code
      ? `Google opened in a new tab. This site is hosted, so Google will send you back to <code>${esc(j.redirect_uri)}</code> on YOUR machine (a dead page, or your local CreatorHaven). Copy the <code>code=</code> value from that tab's address bar and paste it below under “Paste the code”.`
      : `Google opened in a new tab. Redirect: <code>${esc(j.redirect_uri)}</code>. Come back here when it says linked.`;
    window.open(j.url, '_blank');
  };
  $('#importBtn').onclick = async () => { const j = await api('/api/channels/import', { method: 'POST', body: {} }); $('#linkMsg').textContent = j.imported?.length ? `Imported: ${j.imported.join(', ')} (read scope only — re-link for analytics)` : 'Nothing new to import.'; loadSettings(); };
  $('#addPubBtn').onclick = async () => { const el = $('#pubMsg'); el.textContent = 'Resolving + pulling public data…'; const j = await api('/api/channels/add-public', { method: 'POST', body: { refs: $('#pubRefs').value } }); if (j.error) { el.textContent = j.error; return; } const ok = (j.added || []).filter(a => !a.error); el.textContent = `Added ${ok.length}${(j.failed || []).length ? `, ${j.failed.length} failed` : ''}.`; loadSettings(); loadOverview && loadOverview(); };
  $('#redeemBtn').onclick = async () => { const j = await api('/api/oauth/redeem', { method: 'POST', body: { code: $('#redeemCode').value } }); $('#linkMsg').textContent = j.error ? j.error : `Linked ${j.linked}`; loadSettings(); };
  $('#saveSettings').onclick = async () => {
    const body = { discord_webhook_url: $('#setDiscord').value.trim(), sync_minutes: +$('#setSync').value, scan_per_channel: +$('#setScan').value, analytics_days: +$('#setDays').value, base_url: $('#setBase').value.trim(), google: { client_id: $('#setGid').value.trim(), client_secret: $('#setGsec').value.trim(), client_type: $('#setGtype').value } };
    if ($('#setYtKey').value.trim()) body.youtube_api_key = $('#setYtKey').value.trim();
    if ($('#setPw').value) body.site_password = $('#setPw').value;
    const j = await api('/api/settings', { method: 'POST', body });
    $('#saveMsg').textContent = j.ok ? 'Saved.' : (j.error || 'failed');
    loadSettings();
  };

  // ------------------------------------------------------- channel search
  const qEl = $('#q'), qRes = $('#qres');
  let qTimer = null, qSel = -1, qItems = [];
  const hideRes = () => { qRes.classList.add('hidden'); qSel = -1; };
  function renderRes(j) {
    qItems = j.results || [];
    if (!qItems.length) { qRes.innerHTML = `<div class="qfoot">${j.error ? esc(j.error) : 'No channels found.'}</div>`; qRes.classList.remove('hidden'); return; }
    qRes.innerHTML = qItems.map((r, i) => `<div class="qrow" data-i="${i}"><img src="${esc(r.thumb || '')}" alt=""><div><div class="qt">${esc(r.title)}${r.tracked ? '<span class="tag">tracked</span>' : ''}</div>
      <div class="qm">${esc(r.handle || '')} · ${r.hidden_subs ? 'hidden' : fmt(r.subscribers)} subs · ${fmt(r.views)} views · ${fmt(r.videos)} videos</div></div></div>`).join('') +
      `<div class="qfoot">${j.cached ? 'cached' : `${j.units || 0} quota units`} · Enter opens the first result · ↑↓ to choose</div>`;
    qRes.classList.remove('hidden');
    $$('.qrow', qRes).forEach(el => el.onclick = () => openChannel(qItems[+el.dataset.i].channel_id));
  }
  async function runSearch() {
    const q = qEl.value.trim();
    if (q.length < 2) { hideRes(); return; }
    qRes.innerHTML = '<div class="qfoot">Searching…</div>'; qRes.classList.remove('hidden');
    const j = await api(`/api/search/channels?q=${encodeURIComponent(q)}`);
    renderRes(j);
    refreshStatus();
  }
  qEl.oninput = () => { clearTimeout(qTimer); const q = qEl.value.trim(); if (q.length < 2) { hideRes(); return; } qTimer = setTimeout(runSearch, /^@|youtube\.com|^UC[\w-]{20,}/.test(q) ? 300 : 900); };
  qEl.onkeydown = e => {
    if (e.key === 'Escape') { hideRes(); qEl.blur(); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); if (!qItems.length) return; qSel = (qSel + (e.key === 'ArrowDown' ? 1 : -1) + qItems.length) % qItems.length; $$('.qrow', qRes).forEach((el, i) => el.classList.toggle('sel', i === qSel)); }
    else if (e.key === 'Enter') { clearTimeout(qTimer); if (qItems.length && !qRes.classList.contains('hidden')) openChannel(qItems[Math.max(0, qSel)].channel_id); else runSearch(); }
  };
  qEl.onfocus = () => { if (qItems.length) qRes.classList.remove('hidden'); };
  if ($('#qgo')) $('#qgo').onclick = () => { const q = qEl.value.trim(); if (!q) { qEl.focus(); return; } if (qItems.length && !qRes.classList.contains('hidden')) openChannel(qItems[Math.max(0, qSel)].channel_id); else runSearch(); };
  document.addEventListener('click', e => { if (!$('#search').contains(e.target)) hideRes(); });
  document.addEventListener('keydown', e => { if (e.key === '/' && document.activeElement !== qEl && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); qEl.focus(); qEl.select(); } });

  function loadLookupTab() { if (state.lookup) renderLookup(state.lookup); }
  async function openChannel(cid, refresh = false) {
    hideRes();
    showTab('lookup');
    $('#lookup').innerHTML = '<p class="muted">Loading channel…</p>';
    const j = await api(`/api/lookup/channel/${cid}?videos=60${refresh ? '&refresh=1' : ''}`);
    if (!j.ok) { $('#lookup').innerHTML = `<p class="err">${esc(j.error || 'failed')}</p>`; return; }
    state.lookup = j; renderLookup(j); refreshStatus();
  }
  function miniBars(el, rows, color = cssVar('--faint')) {
    const W = el.clientWidth || 700, H = 200, P = { l: 44, r: 8, t: 10, b: 26 };
    if (!rows.length) { el.innerHTML = '<p class="muted">No uploads.</p>'; return; }
    const max = Math.max(1, ...rows.map(r => r.y)); const bw = (W - P.l - P.r) / rows.length;
    let g = '';
    for (let i = 0; i <= 4; i++) { const y = H - P.b - (i / 4) * (H - P.t - P.b); g += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" stroke="${cssVar('--line')}"/><text x="${P.l - 6}" y="${y + 4}" fill="${cssVar('--muted')}" font-size="10" text-anchor="end">${fmt(max * i / 4)}</text>`; }
    rows.forEach((r, i) => {
      const h = (r.y / max) * (H - P.t - P.b);
      const ox = r.out == null ? '' : (Math.round(r.out * 10) / 10) + '×';
      g += `<rect class="bar" x="${P.l + i * bw + 1}" y="${H - P.b - h}" width="${Math.max(1, bw - 2)}" height="${h}" fill="${r.hot ? cssVar('--trail') : '#3f3f46'}" data-t="${esc(r.label || '')}" data-v="${full(r.y)}" data-o="${esc(ox)}" data-hot="${r.hot ? 1 : 0}"></rect>`;
    });
    const step = Math.ceil(rows.length / 6);
    rows.forEach((r, i) => { if (i % step === 0) g += `<text x="${P.l + i * bw + bw / 2}" y="${H - 8}" fill="${cssVar('--muted')}" font-size="10" text-anchor="middle">${r.x}</text>`; });
    el.innerHTML = `<div class="barwrap"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:${H}px">${g}</svg><div class="bar-tip"></div></div>`;
    // rich hover bubble: video title + real views + real outlier ×
    const wrap = el.querySelector('.barwrap'), tip = el.querySelector('.bar-tip');
    if (!wrap || !tip) return;
    wrap.addEventListener('mousemove', e => {
      const rect = e.target.closest && e.target.closest('rect.bar');
      if (!rect) { tip.classList.remove('show'); return; }
      const o = rect.dataset.o, hot = rect.dataset.hot === '1';
      tip.innerHTML = `<div class="bt-title">${rect.dataset.t || 'Untitled'}</div><div class="bt-meta"><span>${rect.dataset.v} views</span>${o ? `<span class="bt-out ${hot ? 'hot' : 'cold'}">${o} outlier</span>` : ''}</div>`;
      tip.classList.add('show');
      const b = wrap.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
      let x = e.clientX - b.left + 14, y = e.clientY - b.top + 14;
      if (x + tw > b.width) x = e.clientX - b.left - tw - 14;
      if (y + th > b.height) y = e.clientY - b.top - th - 14;
      tip.style.left = Math.max(0, x) + 'px'; tip.style.top = Math.max(0, y) + 'px';
    });
    wrap.addEventListener('mouseleave', () => tip.classList.remove('show'));
  }
  const WIN_LABELS = { '28d': '28 days', '3mo': '3 months', '6mo': '6 months', '1yr': '1 year', 'lifetime': 'Lifetime' };
  function renderLookup(j) {
    const c = j.channel, m = j.metrics, g = j.growth || {};
    const W = j.windows || {};
    const wk = (state.lkWin && W[state.lkWin]) ? state.lkWin : '28d';
    const ws = W[wk] || {};
    // channel-wide views over the selected period (residual/evergreen decay model, summed
    // over the uploads we fetched) — this replaces the old per-video "avg views" as the
    // headline number, which is what Studio's "28 day views" actually measures.
    const wsub = wk === 'lifetime'
      ? '<div class="d muted">all-time total</div>'
      : `<div class="d muted">est · residual model${ws.boost ? ` · +${fmt(ws.boost)} catalog lift` : ''}${ws.sample ? ` · ${ws.sample} uploads` : ''}</div>`;
    const d = (x, key) => x ? `<div class="d ${x[key] >= 0 ? 'up' : 'down'}">${x[key] >= 0 ? '+' : ''}${fmt(x[key])} / ${x.days}d</div>` : '<div class="d muted">tracking since today</div>';
    const tiles = [
      ['Subscribers', c.hidden_subs ? 'hidden' : fmt(c.subscribers), d(g.d30 || g.d7, 'subscribers')],
      ['Views · ' + WIN_LABELS[wk], fmt(wk === 'lifetime' ? c.views : ws.views), wsub],
      ['Total views', fmt(c.views), d(g.d30 || g.d7, 'views')],
      ['Videos', fmt(c.videos), d(g.d30 || g.d7, 'videos')],
      ['Avg views / video', fmt(m.avg_views), `<div class="d muted">last ${m.sample} · median ${fmt(m.median_views)}</div>`],
      ['Uploads / week', m.uploads_per_week ?? '–', `<div class="d muted">${m.shorts_share ?? 0}% shorts</div>`],
      ['Views / day (lifetime)', fmt(m.views_per_day_lifetime), `<div class="d muted">${m.channel_age_days ? Math.round(m.channel_age_days / 365 * 10) / 10 + ' yrs old' : ''}</div>`],
      ['Views per sub', m.views_per_sub ?? '–', `<div class="d muted">avg ${fmt(m.avg_likes)} likes · ${fmt(m.avg_comments)} comments</div>`],
    ];
    const winSeg = `<div class="seg lk-winseg">${Object.keys(WIN_LABELS).map(k =>
      `<button data-w="${k}" class="${k === wk ? 'on' : ''}">${k === 'lifetime' ? 'Lifetime' : k}</button>`).join('')}</div>`;
    // click-to-sort recent uploads (Views = most popular, Published = oldest/newest, etc.)
    const sort = state.lkSort || (state.lkSort = { key: 'published_at', dir: 'desc' });
    const NUMK = { views: 1, views_per_day: 1, outlier: 1, likes: 1, comments: 1, duration_s: 1 };
    const vids = [...j.videos].sort((a, b) => { const k = sort.key, s = sort.dir === 'asc' ? 1 : -1;
      return (NUMK[k] ? (+a[k] || 0) - (+b[k] || 0) : String(a[k] || '').localeCompare(String(b[k] || ''))) * s; });
    const sortArrow = k => sort.key === k ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : '';
    const thSort = (k, lbl) => `<th data-sort="${k}" class="thsort${sort.key === k ? ' sorted' : ''}">${lbl}${sortArrow(k)}</th>`;
    // the bar chart always reads chronologically, independent of the table sort
    const chart = [...j.videos].sort((a, b) => (a.published_at || '').localeCompare(b.published_at || ''))
      .map(v => ({ x: (v.published_at || '').slice(5, 10), y: v.views || 0, label: v.title, hot: (v.outlier || 0) >= 2, out: v.outlier }));
    $('#lookup').innerHTML = `
      ${c.banner ? `<div class="lk-banner" style="background-image:url('${esc(c.banner)}=w1600')"></div>` : ''}
      <div class="lk-head"><img class="av" src="${esc(c.thumb || '')}" alt=""><div>
        <h2>${esc(c.title)}</h2>
        <div class="sub"><a href="https://www.youtube.com/${esc(c.handle || 'channel/' + c.channel_id)}" target="_blank">${esc(c.handle || c.channel_id)}</a> · joined ${dateS(c.published_at)}${c.country ? ' · ' + esc(c.country) : ''} · <span class="mono">${esc(c.channel_id)}</span></div>
        <div class="row mt"><button id="lkTrack" class="${j.tracked ? 'ghost' : 'primary'}" ${j.tracked ? 'disabled' : ''}>${j.tracked ? 'Tracked (daily snapshots on)' : 'Track this channel'}</button><button id="lkRefresh" class="ghost">Refresh</button><span class="muted small">${j.cached ? 'cached' : (j.units || 0) + ' quota units'}</span></div>
      </div></div>
      ${winSeg}
      <div class="tiles">${tiles.map(([k, v, dd]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div>${dd}</div>`).join('')}</div>
      <div class="panel"><h3>Views of the last ${vids.length} uploads <span class="muted small">(orange = 2x+ the channel's median)</span></h3><div id="lkChart"></div></div>
      <div class="panel lk-videos"><h3>Recent uploads <span class="muted small">click a column to sort</span></h3><table><thead><tr><th></th>${thSort('title', 'Title')}${thSort('published_at', 'Published')}${thSort('views', 'Views')}${thSort('views_per_day', 'Views/day')}${thSort('outlier', 'Outlier')}${thSort('likes', 'Likes')}${thSort('comments', 'Comments')}${thSort('duration_s', 'Length')}</tr></thead><tbody>
      ${vids.map(v => `<tr><td><a href="https://youtu.be/${v.video_id}" target="_blank"><img src="${esc(v.thumb || '')}" alt=""></a></td><td class="vt"><a href="https://youtu.be/${v.video_id}" target="_blank">${esc(v.title)}</a>${v.is_short ? ' <span class="tag">short</span>' : ''}</td><td>${dateS(v.published_at)}</td><td>${full(v.views)}</td><td>${fmt(v.views_per_day)}</td><td class="outlier ${(v.outlier || 0) >= 2 ? 'hot' : ''}">${v.outlier != null ? v.outlier + '×' : '–'}</td><td>${fmt(v.likes)}</td><td>${fmt(v.comments)}</td><td>${dur(v.duration_s)}</td></tr>`).join('')}
      </tbody></table></div>`;
    miniBars($('#lkChart'), chart);
    $$('#lookup .lk-winseg button').forEach(b => b.onclick = () => { state.lkWin = b.dataset.w; renderLookup(j); });
    $$('#lookup th[data-sort]').forEach(th => th.onclick = () => {
      const k = th.dataset.sort;
      if (state.lkSort.key === k) state.lkSort.dir = state.lkSort.dir === 'asc' ? 'desc' : 'asc';
      else state.lkSort = { key: k, dir: k === 'title' ? 'asc' : 'desc' };
      renderLookup(j);
    });
    $('#lkRefresh').onclick = () => openChannel(c.channel_id, true);
    $('#lkTrack').onclick = async () => { $('#lkTrack').disabled = true; $('#lkTrack').textContent = 'Adding…'; const r = await api('/api/channels/add-public', { method: 'POST', body: { refs: [c.channel_id] } }); if (r.error) { toast(r.error); $('#lkTrack').disabled = false; $('#lkTrack').textContent = 'Track this channel'; return; } toast('Tracking ' + c.title); openChannel(c.channel_id, true); };
  }

  // ============================================================== STUDIO (AI suite)
  $$('#tab-studio .studio-nav button').forEach(b => b.onclick = () => setStudioSec(b.dataset.ssec));
  function setStudioSec(sec) {
    state.ssec = sec;
    $$('#tab-studio .studio-nav button').forEach(b => b.classList.toggle('on', b.dataset.ssec === sec));
    $$('#tab-studio .studio-sec').forEach(s => s.classList.toggle('on', s.id === 'ssec-' + sec));
    if (sec === 'generate') initGenerate();
    document.body.classList.toggle('brain-active', sec === 'strategy');
  }
  let studioInit = false;
  async function loadStudio() {
    if (!studioInit) {
      studioInit = true;
      if (!state.channels) { const cj = await api('/api/channels'); state.channels = cj.channels || []; }
      $('#stChan').innerHTML = '<option value="">All my channels</option>' +
        (state.channels || []).map(c => `<option value="${c.channel_id}">${esc(c.title || c.channel_id)}</option>`).join('');
      renderSuggest(); wirePredict(); wireReview(); loadStrategy();
    }
    setStudioSec(state.ssec || 'strategy');
  }

  // ---- strategy brain: the Studio Brain canvas ------------------------------
  // One centered vertical stream. User rows and AI rows are frameless; the outlier
  // radar is a slate INSIDE the stream (never a side column); idea batches and
  // reports are AI turns; every [thumb:ID] the model cites that the radar knows
  // renders as an inline spotlight slate with the real figures.
  const stHistory = [];
  const YT = id => `https://www.youtube.com/watch?v=${id}`;
  const MQ = id => `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
  const THUMB_RE = /\[thumb:([A-Za-z0-9_-]{11})\]/g;
  const RADAR_TAG = '[ OUTLIER_RADAR // TARGET_IDENTIFIED ]';
  const typing = m => `<span class="typing"><i></i><i></i><i></i></span>${m ? `<span class="ml-3 text-sm text-zinc-500">${esc(m)}</span>` : ''}`;
  const errBox = e => `<span class="text-red-400 text-sm">${esc(e || 'failed')}</span>`;
  const focus = () => $('#stChan').value || null;
  let rdData = null, rdShowAll = false;
  const rdIndex = () => { const m = {}; for (const f of ['network', 'niche']) (rdData?.[f] || []).forEach(r => { if (!m[r.video_id]) m[r.video_id] = r; }); return m; };

  // -- inline markdown → clean typography (bold signposts, code, bare links)
  const inline = s => esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  // one spotlight slate: thumbnail | multiple · channel / title / figures / extra body / Convert
  function slate(r, extra = '') {
    const x = r.outlier != null ? `${r.outlier}×` : (r.multiple || '');
    const xn = parseFloat(x) || 0;
    const figs = [r.views != null ? `${fmt(r.views)} views` : '', r.median ? `vs ~${fmt(r.median)} normal` : '',
      r.age_days != null ? `${Math.round(r.age_days)}d` : '', r.is_short != null ? (r.is_short ? 'short' : 'long') : ''].filter(Boolean).join(' · ');
    return `<div class="st-slate">
      <a class="st-slate-thumb" href="${YT(r.video_id)}" target="_blank" rel="noopener"><img src="${esc(r.thumb || MQ(r.video_id))}" alt="" loading="lazy"></a>
      <div class="min-w-0 flex-1 space-y-2">
        <div class="flex flex-wrap items-center gap-3">${x ? `<span class="st-x ${xn >= 10 ? 'hot' : xn >= 4 ? 'warm' : ''}">${esc(x)}</span>` : ''}
          <span class="text-xs text-zinc-500 truncate">${esc(r.channel || '')}${r.subs ? ` · ${fmt(r.subs)} subs` : ''}</span>${r.mechanism ? `<span class="st-pill">${esc(r.mechanism)}</span>` : ''}</div>
        <a class="block text-white text-[15px] font-medium leading-snug hover:text-amber-300 transition-colors" href="${YT(r.video_id)}" target="_blank" rel="noopener">${esc(r.title || r.video_id)}</a>
        ${figs ? `<div class="st-figs">${figs}</div>` : ''}${extra}
        <div class="pt-2"><button class="st-btn" data-convert="${r.video_id}">Convert →</button></div>
      </div></div>`;
  }
  // media below a paragraph: radar-known ids become slates, unknown ids a bare thumbnail card
  function mediaCards(ids) {
    const idx = rdIndex(), seen = new Set();
    const list = ids.filter(id => !seen.has(id) && seen.add(id));
    const known = list.filter(id => idx[id]), plain = list.filter(id => !idx[id]);
    return known.map(id => `<div class="mt-6"><span class="st-tag">${RADAR_TAG}</span>${slate(idx[id])}</div>`).join('') +
      (plain.length ? `<div class="st-mediarow">${plain.map(id => `<a class="st-media" href="${YT(id)}" target="_blank" rel="noopener"><img src="${MQ(id)}" alt="" loading="lazy"></a>`).join('')}</div>` : '');
  }
  // block markdown: headers, bullet/numbered lists, paragraphs; thumbs collected per block and
  // emitted as an isolated media row right after it (never inside the text)
  function renderMd(text) {
    const src = String(text || '').replace(/\[\[\s*pitched\s*:[^\]]*\]\]\s*$/i, '').replace(/\r/g, '');
    const out = []; let para = [], list = null, listType = '', ids = [];
    const grab = s => s.replace(THUMB_RE, (_, id) => { ids.push(id); return ''; }).trim();
    const flushMedia = () => { if (ids.length) { out.push(mediaCards(ids)); ids = []; } };
    const flushPara = () => { if (para.length) { out.push(`<p>${para.map(inline).join('<br>')}</p>`); para = []; } };
    const flushList = () => { if (list) { out.push(`<${listType}>${list.map(li => `<li>${inline(li)}</li>`).join('')}</${listType}>`); list = null; } };
    for (const raw of src.split('\n')) {
      const line = grab(raw);
      const h = /^#{1,4}\s+(.+)$/.exec(line), ul = /^[-*•]\s+(.+)$/.exec(line), ol = /^\d+[.)]\s+(.+)$/.exec(line);
      if (!line || /^([-*_]\s*){3,}$/.test(line)) { flushPara(); flushList(); flushMedia(); continue; }   // blank line or a --- rule: a breath, never a separator
      if (h) { flushPara(); flushList(); flushMedia(); out.push(`<h3 class="st-h">${inline(h[1])}</h3>`); continue; }
      if (ul || ol) { flushPara(); const t = ul ? 'ul' : 'ol'; if (list && listType !== t) flushList(); if (!list) { list = []; listType = t; } list.push((ul || ol)[1]); continue; }
      if (list) { flushList(); flushMedia(); }
      para.push(line);
    }
    flushPara(); flushList(); flushMedia();
    return out.join('');
  }

  // -- stream rows
  function turn(role, html, cls = '') {
    const d = document.createElement('div');
    d.className = `st-turn animate-fade-in-up ${role === 'user' ? 'st-user' : 'st-ai'} ${cls}`.trim();
    d.innerHTML = `<div class="st-who">${role === 'user' ? '<span class="st-dot bg-zinc-500"></span>You' : '<span class="st-dot bg-amber-500"></span>Studio Brain'}</div><div class="st-body">${html}</div>`;
    $('#stLog').appendChild(d); return d;
  }
  const bodyOf = d => $('.st-body', d);
  const scrollBottom = (smooth = true) => requestAnimationFrame(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: smooth ? 'smooth' : 'auto' }));
  // paragraphs of a fresh reply slide in one after another
  function stagger(d) { $$('.st-body > .st-md > *, .st-body > .st-idea', d).forEach((el, i) => { el.classList.add('animate-fade-in-up'); el.style.animationDelay = `${Math.min(i, 12) * 55}ms`; }); }
  function showReply(d, text) { d._mdText = text; bodyOf(d).innerHTML = `<div class="st-md">${renderMd(text)}</div>`; stagger(d); }
  function greeting(lead) {
    return turn('bot', `<div class="st-md"><p><i class="cursive text-[26px] text-white">${esc(lead)}</i></p><p class="text-zinc-400">I read the outlier radar first (what is beating its own channel's median in your network and across the niche), then convert the patterns into ideas, titles and thumbnails in your voice. Pick a prompt, or just ask.</p></div>`);
  }

  // -- quick prompts: loose at the top, fade out once the conversation starts
  const pillsGone = g => $('#stSuggest').classList.toggle('gone', g);
  function renderSuggest() {
    const chips = [
      { t: 'Convert the top outliers into 5 ideas', act: () => pitchIdeas() },
      { t: 'What is hitting right now, and why?', act: () => outlierReport() },
      { t: 'Punchier titles for my newest upload', act: () => quickAsk('Take my newest upload and give me 6 punchier titles. Use the outlier radar to justify each structure.') },
      { t: 'Thumbnail concepts for my next video', act: () => quickAsk('Give me 3 thumbnail concepts for my next video, each built on a different outlier pattern from the radar: subject, emotion, text, colors.') },
      { t: 'Where is this channel stuck?', act: () => quickAsk('Diagnose this channel like a strategist: what is working, what is not, which outlier patterns from the radar it should be running as a series, and a 4-week plan.') },
    ];
    $('#stSuggest').innerHTML = chips.map((c, i) => `<button class="st-pill-btn" data-i="${i}">${c.t}</button>`).join('');
    $$('#stSuggest button').forEach(b => b.onclick = () => { pillsGone(true); chips[+b.dataset.i].act(); });
    $('#stShowPrompts').onclick = () => { const g = !$('#stSuggest').classList.contains('gone'); pillsGone(g); if (!g) window.scrollTo({ top: 0, behavior: 'smooth' }); };
    $('#stShowSettings').onclick = () => { const s = $('#stSettings'); s.classList.toggle('hidden'); $('#stShowSettings').classList.toggle('on', !s.classList.contains('hidden')); if (!s.classList.contains('hidden')) s.scrollIntoView({ behavior: 'smooth', block: 'start' }); };
    $('#stShowRadar').onclick = () => showRadar();
  }
  function quickAsk(text) { $('#stMsg').value = text; sendStrategy(); }

  // -- console: auto-grow, amber send state, image attachments (downscaled client-side)
  const stImages = [];
  function readyState() { const on = $('#stMsg').value.trim().length > 0 || stImages.length > 0; if (on) $('#stSend').setAttribute('data-ready', ''); else $('#stSend').removeAttribute('data-ready'); }
  function renderAttach() {
    $('#stAttach').innerHTML = stImages.map((im, i) => `<div class="relative group"><img class="h-16 w-auto rounded-sm border border-zinc-800 block" src="data:${im.mime};base64,${im.b64}" alt="">
      <button class="absolute -top-2 -right-2 w-5 h-5 rounded-full bg-neutral-950 border border-zinc-700 text-zinc-400 text-[11px] leading-none grid place-items-center hover:text-red-400 hover:bg-neutral-950" data-unattach="${i}" title="Remove">×</button></div>`).join('');
    $$('#stAttach [data-unattach]').forEach(b => b.onclick = () => { stImages.splice(+b.dataset.unattach, 1); renderAttach(); readyState(); });
    $('#stDockNote').textContent = stImages.length ? `${stImages.length} image${stImages.length > 1 ? 's' : ''} attached` : '';
  }
  function attachFile(f) {
    if (!f || !f.type.startsWith('image/')) return;
    const img = new Image(); const url = URL.createObjectURL(f);
    img.onload = () => {
      const k = Math.min(1, 1600 / Math.max(img.width, img.height));
      const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      stImages.push({ mime: 'image/jpeg', b64: c.toDataURL('image/jpeg', .86).split(',')[1], name: f.name });
      URL.revokeObjectURL(url); renderAttach(); readyState();
    };
    img.src = url;
  }
  if ($('#stMsg')) {
    const ta = $('#stMsg');
    ta.addEventListener('input', readyState);
    ta.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendStrategy(); } });
    ta.addEventListener('paste', e => { for (const it of e.clipboardData?.items || []) if (it.type.startsWith('image/')) { attachFile(it.getAsFile()); e.preventDefault(); } });
    $('#stSend').onclick = sendStrategy;
    $('#stImg').onclick = () => $('#stImgFile').click();
    $('#stImgFile').onchange = () => { [...$('#stImgFile').files].forEach(attachFile); $('#stImgFile').value = ''; };
    $('#stDock').addEventListener('dragover', e => { e.preventDefault(); });
    $('#stDock').addEventListener('drop', e => { e.preventDefault(); [...e.dataTransfer.files].forEach(attachFile); });
    // one delegated listener for every button the stream renders
    $('#stLog').addEventListener('click', e => {
      const t = e.target.closest('[data-convert],[data-paint],[data-plan],[data-feed],#rdRefresh,#rdMore');
      if (!t) return;
      if (t.dataset.convert) pitchIdeas(t.dataset.convert);
      else if (t.dataset.paint) paintIdea(+t.dataset.paint);
      else if (t.dataset.plan) planIdea(+t.dataset.plan);
      else if (t.dataset.feed) { state.rdFeed = t.dataset.feed; renderRadar(); }
      else if (t.id === 'rdRefresh') refreshRadar(true);
      else if (t.id === 'rdMore') { rdShowAll = !rdShowAll; renderRadar(); }
    });
  }
  async function sendStrategy() {
    const msg = $('#stMsg').value.trim(); if (!msg && !stImages.length) return;
    const imgs = stImages.splice(0); $('#stMsg').value = ''; renderAttach(); readyState(); pillsGone(true);
    turn('user', `<div class="st-usertext">${esc(msg)}</div>` + (imgs.length ? `<div class="flex flex-wrap gap-3 mt-4">${imgs.map(i => `<img class="st-att-img" src="data:${i.mime};base64,${i.b64}" alt="">`).join('')}</div>` : ''));
    const sent = msg || 'Look at the attached image and tell me what you see, strategically.';
    stHistory.push({ role: 'user', text: sent });
    const t = turn('bot', typing()); scrollBottom();
    const j = await api('/api/strategy/chat', { method: 'POST', body: { history: stHistory.slice(0, -1), message: sent, channel_id: focus(), images: imgs.map(i => ({ b64: i.b64, mime: i.mime })) } });
    if (j.ok) { showReply(t, j.reply); stHistory.push({ role: 'model', text: j.reply }); }
    else bodyOf(t).innerHTML = errBox(j.error);
    scrollBottom();
  }

  // -- memory + settings (history, pitched titles, notes, radar queries live server-side)
  let stLoaded = false;
  async function loadStrategy() {
    if (stLoaded) return; stLoaded = true;
    const j = await api('/api/strategy/history');
    (j.history || []).forEach(h => { const d = turn(h.role === 'user' ? 'user' : 'bot', h.role === 'user' ? `<div class="st-usertext">${esc(h.text)}</div>` : ''); if (h.role !== 'user') showReply(d, h.text); stHistory.push({ role: h.role, text: h.text }); });
    if (!(j.history || []).length) greeting('How may I help you…'); else pillsGone(true);
    $('#stNotes').value = j.notes || '';
    $('#rdQueries').value = (j.queries || []).join('\n');
    $('#rdAuto').textContent = 'Auto terms right now: ' + ((j.auto_queries || []).join(' · ') || '(none; track some channels first)');
    $('#stSaveSettings').onclick = async () => {
      const r = await api('/api/strategy/settings', { method: 'POST', body: { notes: $('#stNotes').value, queries: $('#rdQueries').value } });
      toast(r.ok ? 'Saved. Refresh the radar to use the new terms.' : (r.error || 'failed'));
    };
    $('#stReset').onclick = async () => {
      if (!confirm('Forget every pitched idea and the chat history? The brain will start pitching from a clean slate.')) return;
      await api('/api/strategy/reset', { method: 'POST' }); stHistory.length = 0; $('#stLog').innerHTML = '';
      greeting('Clean slate. How may I help you…'); pillsGone(false); radarNode(); renderRadar();
    };
    await loadRadar();
    scrollBottom(false);
  }

  // ---- outlier radar: a slate inside the stream -----------------------------
  function radarNode() {
    let n = $('#rdSlate');
    if (!n) {
      n = document.createElement('div'); n.id = 'rdSlate'; n.className = 'st-turn st-radar animate-blur-in';
      n.innerHTML = `<span class="st-tag">${RADAR_TAG}</span>
        <div class="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div class="flex items-center gap-1"><button class="st-feed on" data-feed="niche">Beyond the network</button><button class="st-feed" data-feed="network">Inside the network</button></div>
          <button id="rdRefresh" class="st-btn">Refresh radar</button>
        </div>
        <div id="rdMeta" class="text-xs text-zinc-500 leading-relaxed mb-6"></div>
        <div id="rdList" class="space-y-4"></div>
        <div id="rdFoot" class="pt-4"></div>`;
      $('#stLog').appendChild(n);
    }
    return n;
  }
  // "Radar" in the top line: moves the slate to the end of the stream with a blur-fade
  function showRadar() { const n = radarNode(); $('#stLog').appendChild(n); n.classList.remove('animate-blur-in'); void n.offsetWidth; n.classList.add('animate-blur-in'); renderRadar(); n.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  async function loadRadar() {
    radarNode();
    const j = await api('/api/strategy/radar');
    rdData = j.radar; state.rdHasClient = j.has_client;
    renderRadar();
    if (j.running) return pollRadar();
    const stale = !rdData || (Date.now() / 1000 - (rdData.ts || 0)) > 24 * 3600;
    if (stale && j.has_client) refreshRadar(false);      // first visit / daily: build it (about 1k quota units)
  }
  async function refreshRadar(manual) {
    const r = await api('/api/strategy/radar/refresh', { method: 'POST', body: { queries: $('#rdQueries').value, days: 45, formats: $('#rdShorts').checked ? ['long', 'short'] : ['long'] } });
    if (!r.ok) { if (manual) toast(r.error || 'failed'); return; }
    const b = $('#rdRefresh'); if (b) { b.disabled = true; b.textContent = 'Scanning…'; }
    const m = $('#rdMeta'); if (m) m.textContent = 'Searching the niche and scoring every video against its own channel…';
    pollRadar();
  }
  async function pollRadar() {
    const s = await api('/api/strategy/radar/status');
    const b = $('#rdRefresh');
    if (s.running) { if (b) { b.disabled = true; b.textContent = 'Scanning…'; } return setTimeout(pollRadar, 2500); }
    if (b) { b.disabled = false; b.textContent = 'Refresh radar'; }
    if (s.error) toast('Radar: ' + s.error);
    const j = await api('/api/strategy/radar'); rdData = j.radar; renderRadar();
    // replies rendered before the radar existed get their cited videos upgraded to slates
    $$('#stLog .st-ai').forEach(d => { if (d._mdText && !$('.st-slate', d) && /\[thumb:/.test(d._mdText)) showReply(d, d._mdText); });
  }
  function renderRadar() {
    if (!$('#rdSlate')) return;
    const feed = state.rdFeed || 'niche';
    $$('#rdSlate [data-feed]').forEach(x => x.classList.toggle('on', x.dataset.feed === feed));
    if (!rdData) { $('#rdMeta').textContent = state.rdHasClient ? 'Not built yet.' : 'Link a channel or add a YouTube API key in Settings to scan the niche. The network feed still works from your tracked channels.'; $('#rdList').innerHTML = ''; $('#rdFoot').innerHTML = ''; return; }
    const rows = (rdData[feed] || []).filter(r => feed !== 'niche' || !r.tracked);
    $('#rdMeta').innerHTML = `Built ${esc(ago(rdData.ts))} · last ${rdData.days} days · ${rdData.network?.length || 0} in-network, ${rdData.niche?.length || 0} niche outliers${rdData.units ? ` · ${rdData.units} quota units` : ''}` +
      (rdData.error ? `<br><span class="text-red-400">${esc(rdData.error)}</span>` : '') +
      (rdData.queries?.length ? `<br><span class="font-mono text-[11px] tracking-wide text-zinc-600">${rdData.queries.map(esc).join(' · ')}</span>` : '');
    if (!rows.length) { $('#rdList').innerHTML = `<div class="text-sm text-zinc-500">Nothing above 2x in this feed yet.</div>`; $('#rdFoot').innerHTML = ''; return; }
    const shown = rdShowAll ? rows.slice(0, 40) : rows.slice(0, 8);
    $('#rdList').innerHTML = shown.map(r => slate(r)).join('');
    $('#rdFoot').innerHTML = rows.length > 8 ? `<button id="rdMore" class="st-tb">${rdShowAll ? 'Show fewer' : `Show all ${Math.min(rows.length, 40)}`}</button>` : '';
  }

  // ---- ideas & reports: AI turns in the stream -------------------------------
  const ideaReg = []; // uid -> idea (several batches can live in one stream)
  const IDEA_STYLES = ['gaming', 'mrbeast', 'reaction', 'horror', 'cinematic', 'clean', 'versus', 'vlog', 'explainer'];
  function ideaCard(it, n) {
    const uid = ideaReg.push(it) - 1;
    const idx = rdIndex(), ref = it.ref_id ? idx[it.ref_id] : null;
    const line = (k, v) => v ? `<p><strong>${k}:</strong> ${esc(v)}</p>` : '';
    return `<div class="st-idea pt-2">
      <span class="st-tag">[ IDEA_${String(n + 1).padStart(2, '0')}${it.mechanism ? ` // ${esc(it.mechanism)}` : ''} ]</span>
      <div class="st-slate">
        <div class="min-w-0 flex-1 space-y-4">
          <div class="st-idea-title">${esc(it.title)}</div>
          ${it.channel || it.format ? `<div class="flex flex-wrap gap-2">${it.channel ? `<span class="st-pill">${esc(it.channel)}</span>` : ''}${it.format ? `<span class="st-pill">${esc(it.format)}</span>` : ''}</div>` : ''}
          <div class="st-md space-y-3">${line('Hook', it.hook)}${line('Why', it.why)}${it.thumbnail ? `<p><strong>Thumbnail:</strong> ${esc(it.thumbnail)}${it.thumb_text ? ` <span class="text-zinc-400">(text: <strong>${esc(it.thumb_text)}</strong>)</span>` : ''}</p>` : ''}${line('Series', it.series)}</div>
          ${it.ref_id ? `<div class="pt-1"><span class="st-tag">[ SOURCE${ref ? ` // ${ref.outlier}× ${esc(ref.channel || '')}` : ''} ]</span><a class="st-media mt-0" href="${YT(it.ref_id)}" target="_blank" rel="noopener"><img src="${MQ(it.ref_id)}" alt="" loading="lazy"></a>${it.ref_pattern ? `<div class="text-xs text-zinc-500 mt-2 max-w-sm leading-relaxed">${esc(it.ref_pattern)}</div>` : ''}</div>` : ''}
        </div>
        <div class="w-full md:w-72 shrink-0 space-y-3">
          <div class="st-art" id="ideaArt${uid}">No thumbnail painted yet</div>
          <div class="flex gap-2"><select id="ideaStyle${uid}" class="st-select flex-1 min-w-0">${IDEA_STYLES.map(s => `<option value="${s}">${s}</option>`).join('')}</select><button class="st-btn-gold" data-paint="${uid}">Paint</button></div>
          <button class="st-btn w-full" data-plan="${uid}">Add to calendar</button>
        </div>
      </div></div>`;
  }
  async function pitchIdeas(seedVideo) {
    pillsGone(true);
    const t = turn('bot', typing(seedVideo ? 'Converting that outlier into 5 angles…' : 'Reading the radar and pitching 5 fresh ideas…')); scrollBottom();
    const j = await api('/api/strategy/ideas', { method: 'POST', body: { channel_id: focus(), n: 5, seed_video: seedVideo || null } });
    if (!j.ok) { bodyOf(t).innerHTML = errBox(j.error); return; }
    const idx = rdIndex(), seed = seedVideo ? idx[seedVideo] : null;
    bodyOf(t).innerHTML = `<div class="st-md"><p>${seedVideo ? 'Converted from that outlier' : 'Fresh pitches'} — ${j.ideas.length} angles. Every title here is now remembered, so the next batch will be different.</p>${seed ? `<div class="mt-6"><span class="st-tag">${RADAR_TAG}</span>${slate(seed)}</div>` : ''}</div>` +
      `<div class="space-y-10 mt-10">${j.ideas.map((it, i) => ideaCard(it, i)).join('')}</div>`;
    stagger(t); scrollBottom();
  }
  async function paintIdea(uid) {
    const it = ideaReg[uid]; const box = $(`#ideaArt${uid}`); const btn = $(`#stLog [data-paint="${uid}"]`);
    if (!it || !box) return;
    btn.disabled = true; btn.textContent = 'Painting…'; box.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    const r = await api('/api/strategy/thumb', { method: 'POST', body: { idea: it, style: $(`#ideaStyle${uid}`).value } });
    btn.disabled = false; btn.textContent = 'Paint again';
    if (!r.ok) { box.innerHTML = errBox(r.error); return; }
    box.innerHTML = `<a href="${r.url}" target="_blank"><img src="${r.url}" alt=""></a>`;
  }
  async function planIdea(uid) {
    const it = ideaReg[uid]; if (!it) return;
    const day = prompt('Post on which day? (YYYY-MM-DD)', new Date(Date.now() + 7 * 86400e3).toISOString().slice(0, 10)); if (!day) return;
    const r = await api('/api/plan', { method: 'POST', body: { day, title: it.title, channel_id: focus(), note: (it.hook || '') + (it.thumbnail ? '\nThumb: ' + it.thumbnail : '') } });
    toast(r.ok ? 'Added to the calendar' : (r.error || 'failed'));
  }
  async function outlierReport() {
    pillsGone(true);
    const t = turn('bot', typing('Reading both radar feeds…')); scrollBottom();
    const j = await api('/api/strategy/report', { method: 'POST', body: { channel_id: focus() } });
    if (!j.ok) { bodyOf(t).innerHTML = errBox(j.error); return; }
    const idx = rdIndex();
    bodyOf(t).innerHTML = `<div class="st-md"><h3 class="st-h">What is hitting right now</h3>` +
      (j.trends?.length ? `<p><strong>Trends across them</strong></p><ul>${j.trends.map(x => `<li>${inline(x)}</li>`).join('')}</ul>` : '') + `</div>` +
      `<div class="space-y-8 mt-10">${(j.picks || []).map(p => {
        const r = Object.assign({}, idx[p.video_id] || {}, { video_id: p.video_id, title: p.title, channel: p.channel, mechanism: p.mechanism });
        if (r.outlier == null) r.multiple = p.multiple;
        const extra = `<div class="st-md space-y-3 pt-2">${p.why ? `<p><strong>Why it hit:</strong> ${esc(p.why)}</p>` : ''}${p.convert ? `<p><strong>How we would convert it:</strong> ${esc(p.convert)}</p>` : ''}</div>`;
        return `<div class="st-idea"><span class="st-tag">${RADAR_TAG}</span>${slate(r, extra)}</div>`;
      }).join('')}</div>`;
    stagger(t); scrollBottom();
  }

  // ---- video predictor ------------------------------------------------------
  const secT = s => s == null ? '' : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  const predSpin = m => `<div class="panel" style="text-align:center;padding:44px"><div class="spinner" style="margin:0 auto"></div><p class="muted mt">${esc(m)}</p></div>`;
  const predErr = e => `<div class="panel"><p class="err">${esc(e)}</p></div>`;
  function wirePredict() {
    $('#pvGo').onclick = async () => {
      const source = $('#pvSource').value.trim(), script = $('#pvScript').value.trim();
      if (!source && !script) { toast('Paste a video link or a script'); return; }
      $('#pvOut').innerHTML = predSpin('Modelling the retention curve…');
      const j = await api('/api/predict', { method: 'POST', body: { source, script } });
      $('#pvOut').innerHTML = j.ok ? '' : predErr(j.error); if (j.ok) renderPredict(j);
    };
    $('#pvWatch').onclick = async () => {
      const file = $('#pvFile').files[0], path = $('#pvPath').value.trim();
      if (!file && !path) { toast('Choose an MP4 or enter a local file path'); return; }
      $('#pvProg').textContent = 'Watching the video — sampling frames + audio. This can take a minute for long videos.';
      $('#pvOut').innerHTML = predSpin('Watching your video…');
      let j;
      try {
        if (file) {
          const fd = new FormData(); fd.append('video', file); fd.append('title', file.name);
          j = await (await fetch('/api/predict/upload', { method: 'POST', body: fd })).json();
        } else {
          j = await api('/api/predict', { method: 'POST', body: { path } });
        }
      } catch (e) { j = { ok: false, error: String(e) }; }
      $('#pvProg').textContent = '';
      $('#pvOut').innerHTML = j.ok ? '' : predErr(j.error); if (j.ok) renderPredict(j);
    };
  }
  const NOTE_CLASS = { cut: 'bad', pacing: 'warn', music: 'accent2', sfx: 'accent2', hook: 'warn', visual: '', praise: 'good' };
  function renderPredict(j) {
    const p = j.prediction || {}, v = j.video || {}, dur = j.duration_s || 0;
    const basis = p.watched ? 'watched video' : (p.has_transcript ? 'transcript' : 'title only');
    const basisSub = p.watched ? 'frames + audio analyzed' : (p.has_transcript ? 'higher confidence' : 'paste a script / upload the video');
    $('#pvOut').innerHTML = `
      <div class="tiles pred-tiles">
        <div class="tile"><div class="k">Predicted score</div><div class="v">${p.predicted_score ?? '–'}<span class="tiny">/100</span></div><div class="sub">${esc(p.verdict || '')}</div></div>
        <div class="tile"><div class="k">Avg view duration</div><div class="v">${p.avd_pct ?? '–'}%</div><div class="sub">${dur ? '~' + secT(Math.round(dur * (p.avd_pct || 0) / 100)) + ' of ' + secT(dur) : ''}</div></div>
        <div class="tile"><div class="k">Hook · first 30s</div><div class="v">${p.hook_score ?? '–'}<span class="tiny">/10</span></div></div>
        <div class="tile"><div class="k">Basis</div><div class="v" style="font-size:15px">${basis}</div><div class="sub">${basisSub}</div></div>
      </div>
      ${v.thumb ? `<div class="row" style="gap:14px;margin-bottom:14px"><img src="${v.thumb}" style="width:168px;border-radius:10px"><div><b>${esc(v.title || j.title || '')}</b><div class="tiny">${esc(v.channel_title || '')}${dur ? ' · ' + secT(dur) : ''}</div></div></div>` : ''}
      <div class="panel"><h3>Predicted audience retention <span class="hint">AI estimate · <span class="dot-drop">●</span> drop-off · <span class="dot-replay">●</span> replayed</span></h3><div id="pvChart" class="chart retn" style="height:260px"></div></div>
      ${(p.notes || []).length ? `<div class="panel"><h3>Editor notes <span class="hint">timestamped teardown</span></h3>${p.notes.map(n => `<div class="moment"><span class="ts">${esc(n.ts || (n.t != null ? secT(n.t) : '—'))}</span><span class="pill ${NOTE_CLASS[n.type] === 'good' ? 'good' : NOTE_CLASS[n.type] === 'bad' ? 'bad' : NOTE_CLASS[n.type] === 'warn' ? 'warn' : ''}" style="text-transform:uppercase">${esc(n.type || 'note')}</span><span>${esc(n.note || '')}</span></div>`).join('')}</div>` : ''}
      <div class="grid2">
        <div class="panel"><h3>Biggest drop-offs</h3>${(p.drop_offs || []).map(m => `<div class="moment drop"><span class="ts">${m.t != null ? secT(m.t) : (m.pct || 0) + '%'}</span><span>${esc(m.reason || '')}</span></div>`).join('') || '<p class="muted">None flagged.</p>'}</div>
        <div class="panel"><h3>Most-replayed moments</h3>${(p.replays || []).map(m => `<div class="moment replay"><span class="ts">${m.t != null ? secT(m.t) : (m.pct || 0) + '%'}</span><span>${esc(m.reason || '')} ${'★'.repeat(Math.max(1, Math.min(3, m.strength || 1)))}</span></div>`).join('') || '<p class="muted">None flagged.</p>'}</div>
      </div>
      <div class="panel"><h3>Summary</h3><p>${esc(p.summary || '')}</p>
        ${(p.fixes || []).length ? `<h3 class="mt">Fixes that would lift retention</h3><ol>${p.fixes.map(f => `<li>${esc(f)}</li>`).join('')}</ol>` : ''}
        ${p.packaging ? `<h3 class="mt">Title ideas</h3>${(p.packaging.titles || []).length ? `<ul>${p.packaging.titles.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}${(p.packaging.thumbnails || []).length ? `<h3 class="mt">Thumbnail concepts</h3><ul>${p.packaging.thumbnails.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : (p.packaging.thumbnail ? `<p><b>Thumbnail:</b> ${esc(p.packaging.thumbnail)}</p>` : '')}` : ''}</div>`;
    retentionChart($('#pvChart'), p, dur);
  }
  function retentionChart(el, p, dur) {
    const W = el.clientWidth || 700, H = el.clientHeight || 260, P = { l: 42, r: 14, t: 14, b: 26 };
    const curve = (p.curve || []).slice().sort((a, b) => a.pct - b.pct);
    if (curve.length < 2) { el.innerHTML = '<p class="muted">No curve returned.</p>'; return; }
    const X = pct => P.l + (pct / 100) * (W - P.l - P.r), Y = r => H - P.b - (r / 100) * (H - P.t - P.b);
    let g = '';
    for (let i = 0; i <= 4; i++) { const yv = 100 * i / 4; g += `<line x1="${P.l}" x2="${W - P.r}" y1="${Y(yv)}" y2="${Y(yv)}" stroke="${cssVar('--line')}"/><text x="${P.l - 6}" y="${Y(yv) + 4}" fill="${cssVar('--muted')}" font-size="10" text-anchor="end">${yv}%</text>`; }
    for (let pc = 0; pc <= 100; pc += 25) { const t = dur ? Math.round(dur * pc / 100) : null; g += `<text x="${X(pc)}" y="${H - 8}" fill="${cssVar('--muted')}" font-size="10" text-anchor="middle">${t != null ? secT(t) : pc + '%'}</text>`; }
    const pts = curve.map(c => `${X(c.pct)},${Y(c.retention)}`).join(' ');
    g += `<polyline class="series" fill="none" stroke="${cssVar('--trail')}" stroke-width="1.5" stroke-linejoin="round" points="${pts}"/>`;
    const at = pct => { let best = curve[0]; for (const c of curve) if (Math.abs(c.pct - pct) < Math.abs(best.pct - pct)) best = c; return best.retention; };
    (p.drop_offs || []).forEach(m => { const x = X(m.pct || 0), y = Y(at(m.pct || 0)); g += `<line x1="${x}" x2="${x}" y1="${Y(0)}" y2="${P.t}" stroke="${cssVar('--bad')}" stroke-dasharray="3 3" opacity=".6"/><circle cx="${x}" cy="${y}" r="3.5" fill="${cssVar('--bad')}"/>`; });
    (p.replays || []).forEach(m => { const x = X(m.pct || 0), y = Y(at(m.pct || 0)); g += `<circle cx="${x}" cy="${y}" r="3.5" fill="${cssVar('--good')}"/>`; });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}</svg>`;
  }

  // ---- thumbnail review -----------------------------------------------------
  let rvB64 = null, rvMime = 'image/png';
  function wireReview() {
    const drop = $('#rvDrop'), file = $('#rvFile');
    drop.onclick = () => file.click();
    drop.ondragover = e => { e.preventDefault(); drop.classList.add('drag'); };
    drop.ondragleave = () => drop.classList.remove('drag');
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove('drag'); if (e.dataTransfer.files[0]) readRv(e.dataTransfer.files[0]); };
    file.onchange = () => file.files[0] && readRv(file.files[0]);
    $('#rvGo').onclick = doReview;
  }
  function readRv(f) {
    rvMime = f.type || 'image/png';
    const r = new FileReader();
    r.onload = () => { rvB64 = r.result; const p = $('#rvPrev'); p.classList.remove('hidden'); p.innerHTML = `<img src="${r.result}">`; $('#rvGo').disabled = false; };
    r.readAsDataURL(f);
  }
  async function doReview() {
    if (!rvB64) return;
    $('#rvGo').disabled = true; $('#rvOut').innerHTML = '<div class="spinner"></div>';
    const j = await api('/api/tools/thumbnail-analyzer', { method: 'POST', body: { image_b64: rvB64.split(',')[1], mime: rvMime, title: $('#rvTitle').value } });
    $('#rvGo').disabled = false;
    if (!j.ok) { $('#rvOut').innerHTML = `<p class="err">${esc(j.error)}</p>`; return; }
    const r = j.result, a = r.ai || {}, m = r.metrics || {};
    $('#rvOut').innerHTML = `
      <div class="tiles"><div class="tile"><div class="k">Overall</div><div class="v">${a.overall ?? '–'}<span class="tiny">/100</span></div></div>
        <div class="tile"><div class="k">Clarity</div><div class="v">${a.clarity ?? '–'}</div></div>
        <div class="tile"><div class="k">Contrast</div><div class="v">${a.contrast ?? '–'}</div><div class="sub">measured ${m.contrast}</div></div>
        <div class="tile"><div class="k">Colour</div><div class="v">${a.color ?? '–'}</div><div class="sub">sat ${m.saturation}</div></div>
        <div class="tile"><div class="k">Text</div><div class="v">${a.text_readability ?? '–'}</div></div>
        <div class="tile"><div class="k">Emotion</div><div class="v">${a.emotion ?? '–'}</div></div></div>
      <div class="panel"><p>${m.width}×${m.height} ${m.is_16_9 ? '(16:9 ✓)' : '<span class="warn">(not 16:9)</span>'}</p>
        ${a.error ? `<p class="err">${esc(a.error)}</p>` : `<p><b>At 160px a viewer sees:</b> ${esc(a.small_size_read || '')}</p>
        <p><b>What works:</b> ${(a.what_works || []).map(esc).join(' · ') || '—'}</p>
        <p><b>Fix first:</b> ${(a.fix_first || []).map(esc).join(' · ') || '—'}</p>
        <p><b>Text detected:</b> ${esc(a.text_detected || '—')}</p>`}</div>`;
  }

  // ---- thumbnail generator --------------------------------------------------
  const gen = { style: 'mrbeast', refs: new Set(), styles: [] };
  const REF_LABELS = { face: 'Your face', character: 'Character / avatar', brand: 'Logo / brand', style: 'Style reference' };
  async function initGenerate() {
    const j = await api('/api/thumbs');
    gen.styles = j.styles || [];
    if (!gen.styles.find(s => s.id === gen.style)) gen.style = (gen.styles[0] || {}).id;
    $('#gnStyles').innerHTML = gen.styles.map(s => `<button class="style-chip ${s.id === gen.style ? 'on' : ''}" data-style="${s.id}">${esc(s.name)}<span class="cs">${esc(s.desc)}</span></button>`).join('');
    $$('#gnStyles .style-chip').forEach(b => b.onclick = () => { gen.style = b.dataset.style; $$('#gnStyles .style-chip').forEach(x => x.classList.toggle('on', x === b)); });
    renderRefs(j.refs || {});
    renderGallery(j.outputs || []);
    $('#gnMsg').innerHTML = j.gemini ? '' : '<span class="err">Add a Gemini API key in Settings to generate thumbnails.</span>';
    if ($('#gnImportChan')) {
      $('#gnImportChan').innerHTML = (state.channels || []).map(c => `<option value="${c.channel_id}">${esc(c.title || c.channel_id)}</option>`).join('') || '<option value="">no channels tracked</option>';
      $('#gnImport').onclick = async () => {
        const cid = $('#gnImportChan').value; if (!cid) { toast('No channel to import from'); return; }
        $('#gnMsg').textContent = 'Importing your avatar + top thumbnails…';
        const r = await api('/api/thumbs/import', { method: 'POST', body: { channel_id: cid } });
        if (r.ok) { $('#gnMsg').textContent = `Imported ${(r.added && r.added.character) || 0} avatar + ${(r.added && r.added.style) || 0} thumbnail(s) as references.`; renderRefs(r.refs || {}); }
        else $('#gnMsg').innerHTML = `<span class="err">${esc(r.error)}</span>`;
      };
    }
    $('#gnGo').onclick = doGenerate;
  }
  function renderRefs(refs) {
    $('#gnRefs').innerHTML = Object.keys(REF_LABELS).map(kind => `<div class="refkind">${REF_LABELS[kind]}</div>
      <div class="refgrid" data-kind="${kind}">${(refs[kind] || []).map(r => `<div class="refthumb ${gen.refs.has(kind + ':' + r.id) ? 'on' : ''}" data-kind="${kind}" data-id="${r.id}"><img src="${r.url}"><div class="x" data-x="1">✕</div></div>`).join('')}<div class="refadd" data-kind="${kind}">＋</div></div>`).join('');
    $$('#gnRefs .refthumb').forEach(t => t.onclick = e => {
      const key = t.dataset.kind + ':' + t.dataset.id;
      if (e.target.dataset.x) { api(`/api/thumbs/ref/${t.dataset.kind}/${t.dataset.id}`, { method: 'DELETE' }).then(() => { gen.refs.delete(key); initGenerate(); }); return; }
      if (gen.refs.has(key)) gen.refs.delete(key); else gen.refs.add(key);
      t.classList.toggle('on');
    });
    $$('#gnRefs .refadd').forEach(a => a.onclick = () => pickRef(a.dataset.kind));
  }
  function pickRef(kind) {
    const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*';
    inp.onchange = () => { const f = inp.files[0]; if (!f) return; const r = new FileReader(); r.onload = async () => { const j = await api('/api/thumbs/ref', { method: 'POST', body: { kind, image_b64: r.result.split(',')[1], mime: f.type } }); if (j.ok) { gen.refs.add(kind + ':' + j.ref.id); initGenerate(); } else toast(j.error || 'upload failed'); }; r.readAsDataURL(f); };
    inp.click();
  }
  function renderGallery(outs) { $('#gnGallery').innerHTML = (outs || []).map(o => `<a href="${o.url}" target="_blank"><img src="${o.url}"></a>`).join('') || '<p class="muted">Nothing generated yet.</p>'; }
  async function doGenerate() {
    const subject = $('#gnSubject').value.trim();
    if (!subject && gen.refs.size === 0) { toast('Describe a scene or select a reference'); return; }
    $('#gnGo').disabled = true; $('#gnMsg').textContent = 'Generating… this takes ~10-20s.';
    $('#gnPreview').innerHTML = '<div class="spinner"></div>';
    const refs = [...gen.refs].map(k => ({ kind: k.split(':')[0], id: k.split(':')[1] }));
    const j = await api('/api/thumbs/generate', { method: 'POST', body: { style: gen.style, subject, text: $('#gnText').value, refs } });
    $('#gnGo').disabled = false;
    if (!j.ok) { $('#gnPreview').innerHTML = '<div class="ph">Generation failed</div>'; $('#gnMsg').innerHTML = `<span class="err">${esc(j.error)}</span>`; return; }
    $('#gnMsg').textContent = ''; $('#gnPreview').innerHTML = `<img src="${j.url}?t=${Date.now()}">`;
    const g = await api('/api/thumbs'); renderGallery(g.outputs || []);
  }

  // link-your-own-channel (OAuth) from the Channels rail
  if ($('#linkOwnBtn')) $('#linkOwnBtn').onclick = async () => {
    const j = await api('/api/oauth/start');
    if (j.url) { window.open(j.url, '_blank'); toast('Sign in with the account that owns the channel, then Sync.'); }
    else toast(j.error || 'Set up the Google OAuth client in Settings first');
  };

  // ---------------------------------------------------------------- boot
  (async () => {
    const s = await refreshStatus();
    const params = new URLSearchParams(location.search);
    const linked = params.get('linked'), chParam = params.get('channel'), qParam = params.get('q');
    if (linked) { toast(`Linked ${linked}`); history.replaceState(null, '', '/#settings'); }
    if (s.authed && chParam && /^UC[\w-]{22}$/.test(chParam)) {
      openChannel(chParam, true); history.replaceState(null, '', '/#lookup');   // deep-link from the Studio panel
    } else if (s.authed && qParam) {
      showTab('lookup'); const qi = $('#q'); if (qi) { qi.value = qParam; runSearch(); } history.replaceState(null, '', '/#lookup');
    } else if (s.authed) {
      showTab(location.hash.slice(1) || 'home');
    }
    setInterval(refreshStatus, 60000);
  })();
})();
