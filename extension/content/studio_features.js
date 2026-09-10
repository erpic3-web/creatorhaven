/* CreatorHaven — content/studio_features.js  (studio.youtube.com, isolated world, document_start)
 *
 * Layers a real toolkit onto YouTube Studio. The centrepiece is a FLOATING PANEL (a red "SF"
 * button, bottom-right) that always appears and never depends on Studio's fragile internal markup:
 *
 *   • This channel  — live subs/views/videos pulled from the CreatorHaven site (ext API), with
 *                     one-click "open in CreatorHaven / find outliers".
 *   • Analytics decoder — a searchable glossary of every Studio metric: what it means, how to
 *                     READ it, and what "good" looks like.
 *   • Research     — launches the site's tools (channel search, outliers, idea brain, video
 *                     predictor, thumbnail generator/review, keyword competition, tag generator,
 *                     planner) pre-pointed at the channel you're on.
 *   • Studio tweaks — quick toggles for the on-page add-ons below.
 *
 * Plus best-effort on-page add-ons (gated, additive, fail-silent): ⓘ metric explainers, an
 * outlier badge on the Content list, and an EXPERIMENTAL ad toolbar (place mid-rolls at an
 * interval / remove red ad breaks) that only ever acts on an explicit click + confirm and never
 * auto-saves — you review and press Studio's own Save.
 */
(() => {
  'use strict';
  const api = globalThis.browser ?? globalThis.chrome;
  const SF = globalThis.SF;
  const TAG = '[SF studio+]';
  if (!SF || !api || !api.storage) return;

  let settings = SF.withDefaults({});
  const warned = new Set();
  const warn = (s, e) => { const k = s + ':' + (e && e.message || e); if (!warned.has(k)) { warned.add(k); console.warn(TAG, s, e); } };
  const safe = (s, fn) => { try { return fn(); } catch (e) { warn(s, e); } };
  const el = (t, c, x) => { const n = document.createElement(t); if (c) n.className = c; if (x != null) n.textContent = x; return n; };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
  const site = () => SF.normalizeSiteUrl(settings.siteUrl);
  const channelId = () => SF.channelIdFromPath(location.pathname) || (location.pathname.match(/\/channel\/(UC[\w-]{22})/) || [])[1] || null;
  const fmt = n => (SF.compactNumber ? SF.compactNumber(n) : String(n));

  let toastTimer = 0;
  function toast(text) {
    if (!document.body) return;
    let t = document.querySelector('.sf-sf-toast');
    if (!t) { t = el('div', 'sf-sf-toast'); document.body.appendChild(t); }
    t.textContent = text; t.classList.add('sf-sf-show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('sf-sf-show'), 3400);
  }
  function openSite(pathOrQuery) {
    const u = site() + pathOrQuery;
    window.open(u, '_blank', 'noopener');
  }

  /* ================================================================ analytics decoder */
  const GLOSSARY = [
    ['Views', 'How many times your videos were watched (legit plays).', 'A spike almost always means the packaging (thumbnail + title) earned more clicks — not luck. Compare a video to your own median, not to big channels.', 'Anything above your channel median is a win; 2×+ is an outlier worth repeating.'],
    ['Impressions', 'How many times YouTube SHOWED your thumbnail in browse / suggested.', 'Impressions are supply. Lots of impressions but few views = the thumbnail/title isn\'t converting. Rising impressions after launch = YouTube is testing you wider.', 'Growth over the first 48h is the signal YouTube is pushing it.'],
    ['Impressions click-through rate (CTR)', 'Of the people shown your thumbnail, the % who clicked.', 'Read it WITH impressions — a high CTR on tiny impressions means little. CTR usually falls as impressions scale (colder audiences).', '4–10% is typical for browse; under 2% means fix the packaging.'],
    ['Average view duration (AVD)', 'The average time (mm:ss) a viewer watched.', 'Compare to the video length. A long video can have a lower % but far more watch time. The first 30s decides most of it.', 'Higher is better; aim to beat your last video at the same length.'],
    ['Average percentage viewed', 'The average share of the video people watched.', 'Best read alongside the retention graph — where it dips is where you\'re losing people. Intros and slow middles are the usual culprits.', '>50% on a long video is strong; shorts want ~90%+.'],
    ['Watch time (hours)', 'Total hours watched across everyone.', 'This, more than views, tells YouTube the video is worth recommending. A longer video that holds people can beat a short one on total watch time.', 'Up and to the right; it compounds as a video keeps getting suggested.'],
    ['Audience retention / key moments', 'The line showing what % are still watching at each second.', 'DIPS = people leaving (tighten or cut those spots). BUMPS/spikes = replays (make more of those moments). A cliff in the first 30s is a hook problem.', 'A gentle slope with an intro dip under 20% is healthy.'],
    ['Engaged views', 'Plays where the viewer actually engaged, not just an auto-start.', 'A truer "did they really watch" number than raw views; closer to what advertisers value.', 'The closer engaged views track raw views, the stickier the content.'],
    ['Subscribers', 'Net subscriber change (gained minus lost) for the period.', 'Attribute it to videos — a video that converts viewers to subs is a format to repeat. Losing subs on a video usually means a bait-y title.', 'Any net gain per video is good; big spikes flag a breakout.'],
    ['New vs returning viewers', 'How many viewers are new to you vs coming back.', 'Lots of new viewers = discovery is working (good for growth). High returning = a loyal base (good for launches and community).', 'A healthy channel grows new viewers while keeping returning ones.'],
    ['Unique viewers', 'The estimated number of DIFFERENT people who watched.', 'Views ÷ unique viewers = how many times the average person watched. Useful to tell a few superfans from broad reach.', 'Higher unique viewers = broader reach.'],
    ['Likes / comments / shares', 'Engagement signals on a video.', 'Like rate and comment rate (per view) matter more than raw counts. Shares are the strongest "send to a friend" signal.', 'Like rate ~3–5% of views is solid; comments/shares above your norm = a nerve was hit.'],
    ['Traffic sources', 'Where views come from: Browse, Suggested, Search, External, Shorts feed, Channel pages.', 'Browse + Suggested = the algorithm pushing you (packaging-driven). Search = evergreen (title/keywords). External = off-YouTube. Know which lane a video is in.', 'Suggested-heavy videos scale fastest; search-heavy ones age well.'],
    ['End screen / card CTR', 'The % who clicked an end screen or card.', 'Points viewers to your next video — the bridge that turns one view into a session.', 'End-screen CTR of 5–15% is good; put your best next video there.'],
    ['Revenue (estimated)', 'Your estimated earnings for the period.', 'Driven by RPM × monetized playbacks. Long, mid-roll-friendly content (and finance/tech topics) earns far more per view than shorts.', 'Track RPM trend, not just total — RPM tells you the content\'s ad value.'],
    ['RPM', 'Revenue per 1,000 views AFTER YouTube\'s cut — what you actually keep.', 'Includes ALL views (even non-monetized), so it\'s the real "per 1,000 views" you earn. Movie/long content runs much higher; shorts much lower.', 'Compare your RPM month over month; niche and length move it most.'],
    ['CPM', 'What advertisers pay per 1,000 ad impressions, BEFORE YouTube\'s cut.', 'Always higher than your RPM. High CPM + low RPM means many views weren\'t monetized (shorts, replays, ad blockers).', 'Higher-value niches (finance, B2B) carry higher CPMs.'],
    ['Monetized playbacks', 'Plays that actually showed an ad.', 'Not every view is monetized. The gap between views and monetized playbacks explains a "low earnings on high views" video.', 'A higher monetized share lifts RPM directly.'],
    ['Stayed to watch (Shorts)', 'The % of a Short people watched instead of swiping away.', 'The single most important Shorts metric. The first second and a loop-able ending drive it.', 'Above ~70% is strong for Shorts.'],
    ['Format: long vs Shorts vs Live', 'Which surface a video lives on.', 'They have different physics — judge Shorts on swipe-away and views velocity, long videos on watch time and AVD. Don\'t compare across formats.', 'Use Shorts for reach, long videos for watch time + revenue.']
  ];

  /* ================================================================ the panel */
  let panelEl = null;
  function closePanel() { if (panelEl) { panelEl.classList.remove('sf-sf-open'); } }
  function togglePanel() { buildPanel(); panelEl.classList.toggle('sf-sf-open'); if (panelEl.classList.contains('sf-sf-open')) refreshChannelCard(); }

  function section(title) { const s = el('div', 'sf-sf-sec'); s.appendChild(el('h3', null, title)); return s; }
  function launch(label, sub, onClick) {
    const b = el('button', 'sf-sf-launch'); b.type = 'button';
    b.innerHTML = '<span class="sf-sf-lt">' + esc(label) + '</span><span class="sf-sf-ls">' + esc(sub) + '</span>';
    b.addEventListener('click', onClick);
    return b;
  }

  function buildPanel() {
    if (panelEl && panelEl.isConnected) return;
    panelEl = el('div', 'sf-sf-panel');
    const head = el('div', 'sf-sf-phead');
    head.innerHTML = '<span class="sf-sf-logo">CH</span><b>CreatorHaven</b>';
    const x = el('button', 'sf-sf-px', '✕'); x.type = 'button'; x.title = 'Close'; x.addEventListener('click', closePanel);
    head.appendChild(x);
    panelEl.appendChild(head);

    const body = el('div', 'sf-sf-pbody');

    // This channel
    const chSec = section('This channel');
    const chCard = el('div', 'sf-sf-chcard'); chCard.id = 'sf-sf-chcard';
    chCard.textContent = 'Open a channel or video in Studio to see it here.';
    chSec.appendChild(chCard);
    body.appendChild(chSec);

    // Research
    const rSec = section('Research & tools');
    const cid = channelId();
    const grid = el('div', 'sf-sf-grid');
    grid.appendChild(launch('Search a channel', 'subs, views, outliers', () => openSite('/#lookup')));
    grid.appendChild(launch('Outlier finder', 'this channel\'s best', () => cid ? openSite('/?channel=' + cid) : openSite('/#channels')));
    grid.appendChild(launch('Idea brain', 'titles + ideas', () => openSite('/#studio')));
    grid.appendChild(launch('Predict a video', 'AVD retention curve', () => openSite('/#studio')));
    grid.appendChild(launch('Thumbnail maker', 'any style + your face', () => openSite('/#studio')));
    grid.appendChild(launch('Thumbnail review', 'AI score + fixes', () => openSite('/#studio')));
    grid.appendChild(launch('Keyword check', 'competition + difficulty', () => openSite('/#tools')));
    grid.appendChild(launch('Tag generator', 'ranked, paste-ready', () => openSite('/#tools')));
    grid.appendChild(launch('Content planner', 'schedule + reminders', () => openSite('/#calendar')));
    grid.appendChild(launch('Dashboard', 'the whole network', () => openSite('/#dashboard')));
    rSec.appendChild(grid);
    body.appendChild(rSec);

    // Analytics decoder — collapsed behind a single dropdown (the list is long)
    const dSec = el('details', 'sf-sf-sec sf-sf-group');
    const dSum = el('summary', 'sf-sf-gsum');
    dSum.innerHTML = 'Analytics decoder <span class="sf-sf-gcount">' + GLOSSARY.length + '</span>';
    dSec.appendChild(dSum);
    const search = el('input', 'sf-sf-search'); search.type = 'search'; search.placeholder = 'Search a metric… (CTR, AVD, RPM, retention)';
    const list = el('div', 'sf-sf-gloss');
    function renderGloss(q) {
      q = (q || '').trim().toLowerCase();
      list.innerHTML = '';
      for (const [name, what, read, good] of GLOSSARY) {
        if (q && !(name + ' ' + what + ' ' + read).toLowerCase().includes(q)) continue;
        const item = el('details', 'sf-sf-metric');
        const sum = el('summary', null, name); item.appendChild(sum);
        const w = el('div', 'sf-sf-mrow'); w.innerHTML = '<b>What:</b> ' + esc(what); item.appendChild(w);
        const r = el('div', 'sf-sf-mrow'); r.innerHTML = '<b>How to read:</b> ' + esc(read); item.appendChild(r);
        const g = el('div', 'sf-sf-mrow'); g.innerHTML = '<b>Good looks like:</b> ' + esc(good); item.appendChild(g);
        list.appendChild(item);
      }
      if (!list.childElementCount) list.appendChild(el('p', 'sf-sf-empty', 'No metric matches that.'));
    }
    search.addEventListener('input', () => renderGloss(search.value));
    dSec.appendChild(search); dSec.appendChild(list); renderGloss('');
    body.appendChild(dSec);

    // Studio tweaks
    const tSec = section('On-page add-ons');
    tSec.appendChild(tweak('studioOutlierAll', 'Outlier ×N on the Content list'));
    tSec.appendChild(tweak('studioExplainers', 'ⓘ metric explainers on hover'));
    tSec.appendChild(tweak('studioAdPlacer', 'Ad placer toolbar (experimental)'));
    tSec.appendChild(tweak('studioRemoveRedAds', 'Remove red ad breaks (experimental)'));
    body.appendChild(tSec);

    panelEl.appendChild(body);
    document.body.appendChild(panelEl);
  }
  function tweak(key, label) {
    const row = el('label', 'sf-sf-tweak');
    const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!settings[key];
    cb.addEventListener('change', () => {
      settings[key] = cb.checked;
      api.storage.sync.set({ [key]: cb.checked });
      runAll();
    });
    row.appendChild(cb); row.appendChild(el('span', null, label));
    return row;
  }
  async function refreshChannelCard() {
    const card = document.getElementById('sf-sf-chcard');
    if (!card) return;
    const cid = channelId();
    if (!cid) { card.textContent = 'Open a channel or video in Studio to see it here.'; return; }
    card.innerHTML = '<span class="sf-sf-dim">Loading channel…</span>';
    try {
      const r = await fetch(site() + '/api/ext/channels?ids=' + cid, { headers: { 'X-SF-Token': settings.token || '' } });
      const j = await r.json();
      const c = j && j.channels && j.channels[cid];
      if (c && !c.missing) {
        card.innerHTML =
          (c.thumb ? '<img src="' + esc(c.thumb) + '" alt="">' : '') +
          '<div class="sf-sf-chmeta"><b>' + esc(c.title || cid) + '</b>' +
          '<span>' + fmt(c.subscribers) + ' subs · ' + fmt(c.views) + ' views · ' + fmt(c.videos) + ' videos</span></div>';
        const act = el('div', 'sf-sf-chact');
        const o = el('button', 'sf-sf-launch', 'Open in CreatorHaven'); o.type = 'button'; o.addEventListener('click', () => openSite('/?channel=' + cid));
        act.appendChild(o); card.appendChild(act);
      } else {
        card.innerHTML = '<span class="sf-sf-dim">Not tracked yet.</span>';
        const t = el('button', 'sf-sf-launch', 'Track this channel'); t.type = 'button'; t.addEventListener('click', () => openSite('/?channel=' + cid)); card.appendChild(t);
      }
    } catch (e) {
      card.innerHTML = '<span class="sf-sf-dim">Connect the site in the extension popup to see live stats.</span>';
      const b = el('button', 'sf-sf-launch', 'Open CreatorHaven'); b.type = 'button'; b.addEventListener('click', () => openSite('/')); card.appendChild(b);
    }
  }

  function ensureFab() {
    if (!settings.studioPanel) { document.querySelectorAll('.sf-sf-fab').forEach(f => f.remove()); if (panelEl) { panelEl.remove(); panelEl = null; } return; }
    if (!document.body) return;
    let fab = document.querySelector('.sf-sf-fab');
    if (fab && fab.isConnected) return;
    if (fab) fab.remove();
    fab = el('button', 'sf-sf-fab', 'SF'); fab.type = 'button';
    fab.title = 'CreatorHaven — research & analytics decoder';
    fab.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); safe('toggle', togglePanel); });
    document.body.appendChild(fab);
  }

  /* ================================================================ additive on-page add-ons */
  const METRIC_INFO = {}; GLOSSARY.forEach(([n, what, read]) => { METRIC_INFO[n.toLowerCase().replace(/\s*\(.*\)/, '')] = what + ' ' + read; });
  METRIC_INFO['click-through rate'] = METRIC_INFO['impressions click-through rate'] || 'The % who clicked after being shown your thumbnail.';
  const infoKeys = Object.keys(METRIC_INFO);
  const tagged = new WeakSet();
  function scanExplainers(root) {
    if (!settings.studioExplainers) return;
    safe('explainers', () => {
      const cand = (root || document).querySelectorAll('span, yt-formatted-string, .metric-name, .label');
      let n = 0;
      for (const node of cand) {
        if (++n > 4000) break;
        if (node.childElementCount !== 0) continue;
        if (node.querySelector && node.querySelector('.sf-sf-info')) continue;
        const txt = (node.textContent || '').trim().toLowerCase();
        if (txt && txt.length <= 40 && infoKeys.includes(txt) && !tagged.has(node)) {
          tagged.add(node);
          const b = el('span', 'sf-sf-info', 'i'); b.setAttribute('data-sf-tip', METRIC_INFO[txt]); b.setAttribute('tabindex', '0');
          node.appendChild(b);
        }
      }
    });
  }
  const ROW_SEL = 'ytcp-video-row';
  const parseCount = s => { const m = String(s || '').replace(/,/g, '').trim().match(/^([\d.]+)\s*([KMB])?/i); if (!m) return null; let v = parseFloat(m[1]); if (!isFinite(v)) return null; const u = (m[2] || '').toUpperCase(); if (u === 'K') v *= 1e3; else if (u === 'M') v *= 1e6; else if (u === 'B') v *= 1e9; return v; };
  const median = a => { if (!a.length) return 0; const s = a.slice().sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  function scanOutliers() {
    if (!settings.studioOutlierAll || !/\/videos\b/.test(location.pathname)) return;
    safe('outliers', () => {
      const rows = document.querySelectorAll(ROW_SEL);
      if (rows.length < 4) return;
      const data = [];
      for (const r of rows) { const cell = r.querySelector('#views, .views, [id*="views" i]'); const v = cell ? parseCount(cell.textContent) : null; if (v != null) data.push({ r, v, cell }); }
      if (data.length < 4) return;
      const med = median(data.map(d => d.v)) || 1;
      for (const d of data) {
        const mult = d.v / med;
        let badge = d.r.querySelector('.sf-sf-outlier');
        if (!badge) { badge = el('span', 'sf-sf-outlier'); (d.cell || d.r).appendChild(badge); }
        badge.textContent = (mult >= 1.15 || mult <= 0.85) ? mult.toFixed(1) + '×' : '≈1×';
        badge.title = 'Views vs the median of your ' + data.length + ' loaded uploads (CreatorHaven)';
        badge.classList.toggle('hot', mult >= 2); badge.classList.toggle('cold', mult <= 0.5);
      }
    });
  }

  /* ---- experimental ad toolbar (explicit click + confirm; never auto-saves) ---- */
  const AD_PAGE_RE = /\/(monetization|editor)\b/;
  function ensureAdToolbar() {
    const want = settings.studioAdPlacer || settings.studioRemoveRedAds;
    let bar = document.querySelector('.sf-sf-adbar');
    if (!want || !AD_PAGE_RE.test(location.pathname)) { if (bar) bar.remove(); return; }
    if (bar || !document.body) return;
    bar = el('div', 'sf-sf-adbar');
    bar.appendChild(el('span', 'sf-sf-adtag', 'CreatorHaven · ads'));
    if (settings.studioAdPlacer) { const b = el('button', 'sf-sf-adbtn', 'Place mid-rolls every ' + (settings.studioAdInterval || 5) + ' min'); b.type = 'button'; b.addEventListener('click', () => safe('adplace', placeMidrolls)); bar.appendChild(b); }
    if (settings.studioRemoveRedAds) { const b = el('button', 'sf-sf-adbtn danger', 'Remove red ad breaks'); b.type = 'button'; b.addEventListener('click', () => safe('adremove', removeRedBreaks)); bar.appendChild(b); }
    bar.appendChild(el('span', 'sf-sf-exp', 'experimental — review, then Save in Studio'));
    document.body.appendChild(bar);
  }
  function clickByText(re) { for (const b of document.querySelectorAll('button, ytcp-button, tp-yt-paper-button, [role="button"]')) { if (re.test((b.textContent || '').trim())) { b.click(); return true; } } return false; }
  function placeMidrolls() {
    const mins = Math.max(1, Number(settings.studioAdInterval) || 5);
    if (!confirm('Auto-place mid-roll ad breaks roughly every ' + mins + ' minutes?\nCreatorHaven uses Studio\'s own placement control — review, then Save yourself.')) return;
    toast(clickByText(/add ad breaks automatically|place automatically|automatic ad breaks|add ad breaks/i)
      ? 'Triggered Studio\'s automatic ad-break placement — review the timeline, then Save.'
      : 'Couldn\'t find the ad-break control here. Open the video → Monetization → Ad breaks, then try again.');
  }
  // Detect red ad-break bars by COLOUR + shape (class names change; colour doesn't).
  function cssColorIsRed(v) {
    const m = String(v || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/);
    if (!m) return false;
    const r = +m[1], g = +m[2], b = +m[3], a = m[4] === undefined ? 1 : +m[4];
    return a > 0.3 && r >= 150 && g <= 110 && b <= 110 && (r - g) >= 55 && (r - b) >= 55;
  }
  function elementLooksRed(e) {
    try {
      const cs = getComputedStyle(e);
      if (cssColorIsRed(cs.backgroundColor) || cssColorIsRed(cs.borderTopColor) || cssColorIsRed(cs.borderLeftColor)) return true;
      const cm = (cs.backgroundImage || '').match(/rgba?\([^)]+\)/);
      return cm ? cssColorIsRed(cm[0]) : false;
    } catch (_) { return false; }
  }
  function findRedAdBars() {
    const bars = [], seen = new Set();
    let n = 0;
    for (const e of document.querySelectorAll('div, span, li, button, [class]')) {
      if (++n > 14000) break;
      const r = e.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || r.width > 28 || r.height < 8) continue;   // thin, tall-ish bar
      if (!elementLooksRed(e)) continue;
      const key = Math.round(r.left) + ':' + Math.round(r.top);
      if (seen.has(key)) continue;
      seen.add(key); bars.push(e);
    }
    return bars;
  }
  async function copyText(t) { try { await navigator.clipboard.writeText(t); return true; } catch (_) { return false; } }
  async function removeRedBreaks() {
    if (!confirm('Find red (unapproved) ad breaks and remove them?\nCreatorHaven only clicks controls labelled "remove/delete" — review the result and press Save yourself.')) return;
    const bars = findRedAdBars();
    if (!bars.length) {
      const region = document.querySelector('[class*="ad-break" i], [class*="segment" i], [class*="timeline" i]') || document.body;
      await copyText('[CreatorHaven] ad-break editor markup (no red bars matched by colour):\n' + (region.outerHTML || '').slice(0, 6000));
      toast('No red ad breaks detected by colour. Copied the editor markup to your clipboard — paste it to CreatorHaven and I\'ll wire it exactly.');
      return;
    }
    let removed = 0;
    for (const bar of bars) {
      let ctrl = null, node = bar;
      for (let up = 0; up < 3 && node; up++, node = node.parentElement) {
        ctrl = node.querySelector('[aria-label*="remove" i], [aria-label*="delete" i], [title*="remove" i], [title*="delete" i]');
        if (ctrl) break;
      }
      if (ctrl) { ctrl.click(); removed++; }
    }
    if (removed) { toast('Removed ' + removed + ' of ' + bars.length + ' red ad break(s) — review, then Save.'); return; }
    const s = bars[0];
    const dom = (s.outerHTML || '').slice(0, 1500) + '\n--- parent ---\n' + ((s.parentElement && s.parentElement.outerHTML) || '').slice(0, 3500);
    await copyText('[CreatorHaven] found ' + bars.length + ' red ad-break bars but no labelled remove control. Sample markup:\n' + dom);
    toast('Found ' + bars.length + ' red ad break(s) but not the remove control. Copied the markup — paste it to CreatorHaven to finish it.');
  }

  /* ================================================================ engine */
  const runAll = SF.debounce(() => safe('runAll', () => { ensureFab(); scanExplainers(); scanOutliers(); ensureAdToolbar(); }), 400);
  async function loadSettings() { try { settings = SF.withDefaults(await api.storage.sync.get(SF.SF_DEFAULTS)); } catch (e) { warn('settings', e); } }
  function init() {
    api.storage.onChanged.addListener((c, area) => { if (area === 'sync') loadSettings().then(runAll); });
    loadSettings().then(() => {
      const start = () => {
        safe('observe', () => new MutationObserver(runAll).observe(document.documentElement, { childList: true, subtree: true }));
        runAll();
        setInterval(() => safe('ensureFab', ensureFab), 2500);   // re-assert the FAB across SPA navigations
      };
      if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, { once: true });
      for (const ev of ['yt-navigate-finish', 'yt-page-data-updated']) window.addEventListener(ev, runAll, true);
    });
  }
  init();
})();
