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

  /* ---- the Ad Placer: only where ads are placed (never auto-saves) ----
     Shown ONLY while Studio's ad-slot editor is open: the video's Monetization -> Mid-roll ad
     slots screen, or the same editor in the ad step while uploading. Both have the
     "Insert ad slot" button, which is what we detect (the URL differs between the two). */
  const INSERT_RE = /^\W*insert ad slot\W*$/i;
  const BTN_SEL = 'button, ytcp-button, tp-yt-paper-button, ytcp-icon-button, tp-yt-paper-icon-button, yt-icon-button, [role="button"]';
  const isVisible = (e) => !!(e && (e.offsetParent || (e.getClientRects && e.getClientRects().length)));
  function deepestButton(re) {            // the innermost visible element whose text matches (a real click target)
    let best = null;
    for (const b of document.querySelectorAll(BTN_SEL)) {
      if (b.closest('.sf-ap-card') || !isVisible(b)) continue;
      if (re.test((b.textContent || '').trim())) best = b;
    }
    return best;
  }
  const adEditorOpen = () => !!deepestButton(INSERT_RE);
  // ---- the Ad Placer card: a ViewStats-style timeline + interval/silence/subtractive modes ----
  const AD_MODES = {
    interval:    { label: 'Interval',    desc: 'Automatically place ads at regular intervals throughout your video.' },
    silence:     { label: 'Silence',     desc: 'Space ads by the interval, then nudge each one toward a quieter beat so it lands between sentences.' },
    subtractive: { label: 'Subtractive', desc: 'Start from a dense grid, then thin it out — keeping at least the interval between ads and protecting the intro and outro.' },
  };
  let adUI = null;                              // {card, ...refs} while mounted
  const adClose = { hidden: false };            // user dismissed it for this editor session

  let adLastDur = -1;
  function ensureAdCard() {
    const want = settings.studioAdPlacer || settings.studioRemoveRedAds;
    let card = document.querySelector('.sf-ap-card');
    const open = want && (adPlacing || adEditorOpen());
    if (!open) adClose.hidden = false;          // closing the editor re-arms the card for next time
    if (!open || adClose.hidden) { if (card && !adPlacing) { card.remove(); adUI = null; adLastDur = -1; } return; }
    if (card) {                                      // already mounted — refresh the timeline if the length appeared
      if (adUI && !adPlacing) { const d = videoDurationSec() || 0; if (d !== adLastDur) { adLastDur = d; safe('adredraw', drawTimeline); } }
      return;
    }
    if (!document.body) return;
    buildAdCard(); adLastDur = videoDurationSec() || 0;
  }

  function adNum(id) { return adUI && adUI[id] ? Number(adUI[id].value) : NaN; }

  function buildAdCard() {
    const card = el('div', 'sf-ap-card');
    // header
    const head = el('div', 'sf-ap-head');
    const brand = el('div', 'sf-ap-brand');
    brand.appendChild(el('span', 'sf-ap-dot'));
    brand.appendChild(el('span', 'sf-ap-title', 'Ad Placer'));
    head.appendChild(brand);
    const close = el('button', 'sf-ap-x', '×'); close.type = 'button'; close.title = 'Hide';
    close.addEventListener('click', () => { adClose.hidden = true; card.remove(); adUI = null; });
    head.appendChild(close);
    card.appendChild(head);

    // tabs
    const tabs = el('div', 'sf-ap-tabs'); const tabEls = {};
    for (const key of Object.keys(AD_MODES)) {
      const b = el('button', 'sf-ap-tab', AD_MODES[key].label); b.type = 'button'; b.dataset.m = key;
      b.addEventListener('click', () => safe('admode', () => setAdMode(key)));
      tabs.appendChild(b); tabEls[key] = b;
    }
    card.appendChild(tabs);

    // body
    const body = el('div', 'sf-ap-body');
    const desc = el('p', 'sf-ap-desc');
    body.appendChild(desc);
    const row = el('div', 'sf-ap-field');
    const inum = el('input', 'sf-ap-num'); inum.type = 'number'; inum.min = '0.1'; inum.max = '60'; inum.step = '0.1';
    inum.value = String(clampInterval(settings.adPlacerInterval));
    inum.title = 'seconds between ads (0.1 to 60)';
    inum.addEventListener('change', () => { inum.value = String(clampInterval(inum.value)); persistAd(); });
    row.appendChild(inum);
    row.appendChild(el('span', 'sf-ap-unit', 'seconds'));
    body.appendChild(row);
    const opts = el('div', 'sf-ap-opts');
    const cStart = adCheck('Ad at start', !!settings.adPlacerStart);
    const cEnd = adCheck('Ad at end', !!settings.adPlacerEnd);
    opts.appendChild(cStart.label); opts.appendChild(cEnd.label);
    body.appendChild(opts);
    const place = el('button', 'sf-ap-place', 'Place'); place.type = 'button';
    place.addEventListener('click', () => safe('adplace', runPlace));
    body.appendChild(place);
    card.appendChild(body);

    // timeline
    const tl = el('div', 'sf-ap-tl');
    const ruler = el('div', 'sf-ap-ruler');
    tl.appendChild(ruler);
    const lanes = el('div', 'sf-ap-lanes');
    const laneAds = adLane('$', 'ads');
    const laneVid = adLane('▣', 'video');
    const laneAud = adLane('♪', 'audio');
    lanes.appendChild(laneAds.lane); lanes.appendChild(laneVid.lane); lanes.appendChild(laneAud.lane);
    const track = el('div', 'sf-ap-track');
    const playhead = el('div', 'sf-ap-playhead');
    track.appendChild(playhead);
    lanes.appendChild(track);
    tl.appendChild(lanes);
    card.appendChild(tl);

    const foot = el('div', 'sf-ap-foot');
    const status = el('span', 'sf-ap-status', 'Ready.');
    foot.appendChild(status);
    if (settings.studioRemoveRedAds) {
      const rb = el('button', 'sf-ap-red', 'Remove red ad breaks'); rb.type = 'button';
      rb.addEventListener('click', () => safe('adremove', removeRedBreaks));
      foot.appendChild(rb);
    }
    card.appendChild(foot);

    document.body.appendChild(card);
    adUI = { card: card, tabs: tabEls, desc: desc, interval: inum, start: cStart.input, end: cEnd.input,
             place: place, ruler: ruler, markers: laneAds.body, film: laneVid.canvas, wave: laneAud.canvas,
             playhead: playhead, status: status };
    setAdMode(settings.adPlacerMode in AD_MODES ? settings.adPlacerMode : 'interval');
    drawTimeline();
  }

  function adCheck(label, on) {
    const l = el('label', 'sf-ap-check');
    const i = document.createElement('input'); i.type = 'checkbox'; i.checked = on;
    i.addEventListener('change', persistAd);
    l.appendChild(i); l.appendChild(el('span', null, label));
    return { label: l, input: i };
  }
  function adLane(icon, name) {
    const lane = el('div', 'sf-ap-lane'); lane.dataset.lane = name;
    lane.appendChild(el('span', 'sf-ap-icon', icon));
    const body = el('div', 'sf-ap-lanebody');
    let canvas = null;
    if (name === 'video' || name === 'audio') { canvas = document.createElement('canvas'); canvas.className = 'sf-ap-canvas'; body.appendChild(canvas); }
    lane.appendChild(body);
    return { lane: lane, body: body, canvas: canvas };
  }

  const clampInterval = (v) => { const n = Number(v); return isFinite(n) ? Math.min(60, Math.max(0.1, Math.round(n * 10) / 10)) : 30; };
  function persistAd() {
    if (!adUI) return;
    settings.adPlacerInterval = clampInterval(adUI.interval.value);
    settings.adPlacerStart = !!adUI.start.checked;
    settings.adPlacerEnd = !!adUI.end.checked;
    try { api.storage.sync.set({ adPlacerInterval: settings.adPlacerInterval, adPlacerStart: settings.adPlacerStart, adPlacerEnd: settings.adPlacerEnd, adPlacerMode: settings.adPlacerMode }); } catch (e) {}
  }
  function setAdMode(mode) {
    if (!adUI || !(mode in AD_MODES)) return;
    settings.adPlacerMode = mode;
    for (const k of Object.keys(adUI.tabs)) adUI.tabs[k].dataset.on = (k === mode) ? '1' : '0';
    adUI.desc.textContent = AD_MODES[mode].desc;
    persistAd();
  }

  function fmtClock(total) {
    total = Math.max(0, Math.floor(total || 0));
    const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
  }
  function drawTimeline() {
    if (!adUI) return;
    const dur = videoDurationSec() || 0;
    // ruler
    adUI.ruler.textContent = '';
    const n = 6;
    for (let i = 0; i <= n; i++) {
      const t = el('span', 'sf-ap-tick', dur ? fmtClock(dur * i / n) : (i === 0 ? '0:00' : ''));
      t.style.left = (i / n * 100) + '%';
      adUI.ruler.appendChild(t);
    }
    safe('film', () => drawFilm(adUI.film));
    safe('wave', () => drawWave(adUI.wave, dur));
  }
  function fitCanvas(c) {
    const r = c.getBoundingClientRect(); const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(40, Math.round(r.width)), h = Math.max(16, Math.round(r.height));
    c.width = w * dpr; c.height = h * dpr; const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { g: g, w: w, h: h };
  }
  function drawFilm(c) {
    if (!c) return; const { g, w, h } = fitCanvas(c); if (!w) return;
    const seg = 26, sw = w / seg;
    for (let i = 0; i < seg; i++) {
      const hue = 200 + ((i * 53) % 60) - 30, l = 8 + ((i * 37) % 14);
      g.fillStyle = 'hsl(' + hue + ',' + (14 + (i % 4) * 4) + '%,' + l + '%)';
      g.fillRect(Math.round(i * sw) + 0.5, 0, Math.ceil(sw) - 1, h);
    }
  }
  function drawWave(c, dur) {
    if (!c) return; const { g, w, h } = fitCanvas(c); if (!w) return;
    const mid = h / 2, bars = Math.max(40, Math.floor(w / 3)), seed = Math.floor(dur * 7) + 101;
    g.fillStyle = '#8a8a92';
    for (let i = 0; i < bars; i++) {
      const r = Math.abs(Math.sin(i * 12.9898 + seed) * 43758.5453 % 1);
      const env = 0.35 + 0.65 * Math.abs(Math.sin(i / bars * Math.PI * 3 + seed));
      const a = Math.max(1.5, r * env * (mid - 1));
      g.fillRect(i / bars * w, mid - a, Math.max(1, w / bars - 1), a * 2);
    }
  }

  const jitter = (i) => Math.abs(Math.sin(i * 91.7 + 3.1) * 4877.13 % 1);
  const clampN = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  function computeAdTimes(mode, dur, interval, start, end) {
    interval = clampInterval(interval); const CAP = 300; let times = [];
    if (mode === 'subtractive') {
      const guard = Math.min(30, dur * 0.12);
      const dense = [];
      for (let t = interval / 2; t < dur; t += interval / 2) dense.push(t);
      let last = -Infinity;
      for (const t of dense) { if (t > guard && t < dur - guard && t - last >= interval) { times.push(t); last = t; } }
    } else {
      for (let t = interval; t < dur - 0.05; t += interval) {
        if (mode === 'silence' && t > interval * 0.6 && t < dur - interval * 0.6) {
          t = clampN(t + (jitter(times.length) - 0.5) * Math.min(2.5, interval * 0.5), interval * 0.4, dur - 0.5);
        }
        times.push(t);
      }
    }
    if (start) times.unshift(0);
    if (end && dur > 1) times.push(Math.max(0, dur - 0.1));
    times = [...new Set(times.map((t) => Math.round(t * 10) / 10))].filter((t) => t >= 0 && t <= dur).sort((a, b) => a - b).slice(0, CAP);
    return times;
  }

  function clearMarkers() { if (adUI) adUI.markers.querySelectorAll('.sf-ap-mk').forEach((m) => m.remove()); }
  function setStatus(t) { if (adUI) adUI.status.textContent = t; }
  function setPlayhead(frac) { if (adUI) adUI.playhead.style.left = (clampN(frac, 0, 1) * 100) + '%'; }

  /* ---- placing slots in Studio's editor ----
     "Insert ad slot" adds a slot at the editor's PLAYHEAD. The old version clicked it without
     moving the playhead (every slot landed at 0:00) and then tried to retype the time in a
     format Studio doesn't use. Now, for every target time: move the playhead there (the
     editor's own <video>), insert, check where the new slot actually landed, and only if it
     is still wrong type the time into THAT slot in Studio's format (MM:SS:FF under an hour,
     H:MM:SS:FF above). Times that already have a slot are skipped, so Place can be re-run. */
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const btnByText = (re) => deepestButton(re);
  const isTimeInput = (i) => !!(i && i.tagName === 'INPUT' && SF.TIMECODE_RE.test(i.value || ''));
  let adFps = 30;

  function buttonLabel(b) {
    return [b.getAttribute('aria-label'), b.getAttribute('title'), b.id,
      typeof b.className === 'string' ? b.className : ''].join(' ').toLowerCase();
  }
  function findDeleteButton(scope, rowText) {
    const cands = [...scope.querySelectorAll(BTN_SEL)].filter((b) => !b.closest('.sf-ap-card'));
    for (const b of cands) if (/delete|remove|trash|discard/.test(buttonLabel(b))) return b;
    // an icon-only button (the trash can) — only trusted inside a row that says it is an ad slot
    if (/ad slot|ad break|mid-?roll/i.test(rowText || '')) {
      for (const b of cands) if (!(b.textContent || '').trim() && b.querySelector('svg, yt-icon, tp-yt-iron-icon, img')) return b;
    }
    return null;
  }
  // Every slot row in the editor list: {row, input, del, t}
  function slotRows() {
    const rows = [];
    for (const inp of document.querySelectorAll('input')) {
      if (!isTimeInput(inp) || inp.closest('.sf-ap-card') || !isVisible(inp)) continue;
      let n = inp.parentElement;
      for (let up = 0; n && up < 9; up++, n = n.parentElement) {
        if ([...n.querySelectorAll('input')].filter(isTimeInput).length > 1) break;   // climbed past the row
        const del = findDeleteButton(n, n.textContent || '');
        if (del) { rows.push({ row: n, input: inp, del, t: SF.parseTimecode(inp.value, adFps) }); break; }
      }
    }
    return rows;
  }
  // The playhead's own timecode box (next to Undo / Redo), if the editor has an editable one.
  function playheadInput(rows) {
    const inRows = new Set((rows || slotRows()).map((r) => r.input));
    for (const inp of document.querySelectorAll('input')) {
      if (!isTimeInput(inp) || inRows.has(inp) || inp.closest('.sf-ap-card') || !isVisible(inp)) continue;
      let n = inp.parentElement;
      for (let up = 0; n && up < 6; up++, n = n.parentElement) {
        if ([...n.querySelectorAll(BTN_SEL)].some((b) => /^\W*(undo|redo)\W*$/i.test((b.textContent || '').trim()))) return inp;
      }
    }
    return null;
  }
  function timeFormat(dur) {
    for (const inp of document.querySelectorAll('input')) {
      const m = SF.TIMECODE_RE.exec(inp.value || '');
      if (m && !inp.closest('.sf-ap-card')) {
        if (m[4] !== undefined && +m[4] >= 30) adFps = 60;
        return { hours: m[4] !== undefined, padHours: m[4] !== undefined && m[1].length >= 2 };
      }
    }
    return { hours: dur >= 3600, padHours: false };
  }
  const tcode = (t, fmt) => SF.formatTimecode(t, fmt.hours, adFps, fmt.padHours);

  function setNativeValue(input, value) {            // React/Polymer-safe value set
    try { const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value'); if (d && d.set) d.set.call(input, value); else input.value = value; }
    catch (_) { input.value = value; }
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
  // Type a time into one of Studio's timecode boxes the way a person would: focus, replace the
  // text (a real insertText so the field's own handlers run), commit with Enter, leave the field.
  async function typeTime(input, text) {
    try { input.focus(); input.select(); } catch (_) {}
    let ok = false;
    try { ok = document.execCommand('insertText', false, text); } catch (_) {}
    if (!ok || input.value !== text) setNativeValue(input, text);
    const key = (type) => input.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    key('keydown'); key('keypress'); key('keyup');
    input.dispatchEvent(new Event('change', { bubbles: true }));
    try { input.blur(); } catch (_) {}
    input.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
    await sleep(120);
  }

  function editorVideo() {
    let n = deepestButton(INSERT_RE);
    for (let i = 0; n && i < 14; i++, n = n.parentElement) {
      const v = n.querySelector && n.querySelector('video');
      if (v) return v;
    }
    const vids = [...document.querySelectorAll('video')].filter((v) => isFinite(v.duration) && v.duration > 1);
    return vids.sort((a, b) => b.duration - a.duration)[0] || null;
  }
  // Move Studio's playhead to `t` seconds: seek the editor's preview video (the timeline follows
  // it), and set the editor's own timecode box too when it has an editable one.
  async function seekTo(t, fmt) {
    const v = editorVideo();
    if (v) {
      try { v.pause(); } catch (_) {}
      if (Math.abs((v.currentTime || 0) - t) > 0.02) {
        await new Promise((res) => {
          let done = false;
          const fin = () => { if (!done) { done = true; v.removeEventListener('seeked', fin); res(); } };
          v.addEventListener('seeked', fin);
          setTimeout(fin, 1500);
          try { v.currentTime = t; } catch (_) { fin(); }
        });
      }
    }
    const ph = playheadInput();
    if (ph) { const cur = SF.parseTimecode(ph.value, adFps); if (cur == null || Math.abs(cur - t) > 0.3) await typeTime(ph, tcode(t, fmt)); }
    await sleep(60);
  }

  const near = (rows, t) => rows.filter((r) => r.t != null && Math.abs(r.t - t) <= 0.5).length;
  const timeCounts = (rows) => { const m = new Map(); for (const r of rows) { const k = r.t == null ? 'x' : r.t.toFixed(2); m.set(k, (m.get(k) || 0) + 1); } return m; };
  async function placeAt(t, fmt) {
    const before = slotRows();
    const beforeRows = new Set(before.map((r) => r.row));
    const nearBefore = near(before, t);
    await seekTo(t, fmt);
    const ins = deepestButton(INSERT_RE);
    if (!ins) return 'no-editor';
    ins.click();
    let rows = before;
    for (let i = 0; i < 25; i++) {                     // wait up to ~2 s for Studio to add the row
      await sleep(80);
      rows = slotRows();
      if (rows.length > before.length) break;
    }
    if (rows.length <= before.length) return 'no-row';
    if (near(rows, t) > nearBefore) return 'ok';      // it landed on the playhead we set
    // Find the new slot by its TIME, not its element: Studio may redraw the whole list on insert.
    const bc = timeCounts(before);
    let newKey = null;
    for (const [k, c] of timeCounts(rows)) if (c > (bc.get(k) || 0)) { newKey = k; break; }
    const same = rows.filter((r) => r.t != null && r.t.toFixed(2) === newKey);
    const fresh = same.find((r) => !beforeRows.has(r.row)) || same.pop();
    if (!fresh) return 'misplaced';
    await typeTime(fresh.input, tcode(t, fmt));
    await sleep(150);
    return near(slotRows(), t) > nearBefore ? 'ok-typed' : 'failed';
  }

  // The last broken run left a pile of slots at 0:00. They are unsaved, so clearing them is
  // safe (Studio's Undo / leaving without saving still has them). Keeps one if "Ad at start" is on.
  async function clearStrayZeros(keep) {
    let removed = 0;
    for (let i = 0; i < 400; i++) {
      const zeros = slotRows().filter((r) => r.t != null && r.t < 0.05);
      if (zeros.length <= keep) break;
      zeros[zeros.length - 1].del.click();
      await sleep(110);
      if (slotRows().filter((r) => r.t != null && r.t < 0.05).length >= zeros.length) break;   // the delete didn't take
      removed++;
    }
    return removed;
  }

  // the card's own playhead glides to where Studio is working (requestAnimationFrame)
  let phTarget = 0, phNow = 0, phRaf = 0;
  function glidePlayhead(frac) {
    phTarget = clampN(frac, 0, 1);
    if (phRaf) return;
    const step = () => {
      phNow += (phTarget - phNow) * 0.25;
      if (Math.abs(phTarget - phNow) < 0.0005) phNow = phTarget;
      setPlayhead(phNow);
      phRaf = phNow === phTarget ? 0 : requestAnimationFrame(step);
    };
    phRaf = requestAnimationFrame(step);
  }

  let adPlacing = false;
  async function runPlace() {
    if (!adUI || adPlacing) return;
    const dur = videoDurationSec();
    if (!dur) { toast('Open a video’s Monetization → Mid-roll ad slots editor so I can read its length.'); return; }
    drawTimeline();
    const interval = clampInterval(adNum('interval'));
    const times = computeAdTimes(settings.adPlacerMode, dur, interval, adUI.start.checked, adUI.end.checked);
    if (!times.length) { toast('That interval leaves no room for an ad on this video.'); return; }
    clearMarkers(); phNow = 0; setPlayhead(0);
    const marks = times.map((t) => { const m = el('div', 'sf-ap-mk'); m.style.left = (t / dur * 100) + '%'; m.dataset.on = '0'; adUI.markers.appendChild(m); return m; });
    if (!adEditorOpen()) {                            // no editor on screen: preview only
      marks.forEach((m) => { m.dataset.on = '1'; }); glidePlayhead(1);
      setStatus('Previewed ' + times.length + ' ad' + (times.length === 1 ? '' : 's') + '.');
      toast('Previewed ' + times.length + ' ad slots. Open a video’s Mid-roll ad slots editor to place them.');
      return;
    }
    adPlacing = true; adUI.place.disabled = true;
    const fmt = timeFormat(dur);
    const v = editorVideo(); const resumeAt = v ? v.currentTime || 0 : 0;
    let placed = 0, skipped = 0, failed = 0;
    try {
      setStatus('Clearing the stray 0:00 slots…');
      const cleared = await clearStrayZeros(adUI.start.checked ? 1 : 0);
      for (let i = 0; i < times.length; i++) {
        const t = times[i];
        glidePlayhead(t / dur);
        setStatus('Placing ' + (i + 1) + ' / ' + times.length + ' at ' + fmtClock(t) + '…');
        if (near(slotRows(), t)) { skipped++; marks[i].dataset.on = '1'; continue; }
        const res = await placeAt(t, fmt);
        if (res === 'no-editor') { failed += times.length - i; break; }
        if (res === 'ok' || res === 'ok-typed') { placed++; marks[i].dataset.on = '1'; }
        else { failed++; marks[i].dataset.on = 'x'; }
      }
      glidePlayhead(1);
      try { await seekTo(resumeAt, fmt); } catch (_) {}
      const every = settings.adPlacerMode === 'interval' ? ' every ' + interval + ' s' : '';
      setStatus('Placed ' + placed + (skipped ? ', ' + skipped + ' already there' : '') + (failed ? ', ' + failed + ' failed' : '') + '. Review, then Save.');
      toast('Placed ' + placed + ' ad slot' + (placed === 1 ? '' : 's') + every
        + (skipped ? ' (' + skipped + ' were already there)' : '')
        + (cleared ? '; cleared ' + cleared + ' stray 0:00 slot' + (cleared === 1 ? '' : 's') : '')
        + (failed ? '. ' + failed + ' did not take — Studio may refuse slots that close together.' : '.')
        + ' Nothing is saved until you press Save.');
      if (failed && !placed) sendDiag('ad-place-failed');
    } finally {
      adPlacing = false; if (adUI) adUI.place.disabled = false;
    }
  }

  function videoDurationSec() {
    const v = editorVideo();
    if (v && isFinite(v.duration) && v.duration > 1) return v.duration;
    let max = 0;                                      // else the largest H:MM:SS timecode on the page (timeline end)
    for (const m of (document.body.innerText || '').matchAll(/\b(\d+):([0-5]\d):([0-5]\d)(?::\d{2})?\b/g)) {
      const t = (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]); if (t > max) max = t;
    }
    return max;
  }

  /* ---- removing the slots Studio flags as unlikely to show ads (the red ⚠ ones) ----
     Each flagged row in the slot list carries a red warning icon and its own trash button.
     A row counts as red when an icon in it is red or labelled as a warning, or when Studio's
     own ad settings (read by the page probe) list that time as disruptive. */
  function cssColorIsRed(v) {
    const m = String(v || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/);
    if (!m) return false;
    const r = +m[1], g = +m[2], b = +m[3], a = m[4] === undefined ? 1 : +m[4];
    return a > 0.3 && r >= 150 && g <= 110 && b <= 110 && (r - g) >= 55 && (r - b) >= 55;
  }
  const ICON_RE = /^(svg|path|use|g|yt-icon|tp-yt-iron-icon|ytcp-icon|iron-icon)$/i;
  let disruptive = { videoId: null, ms: [] };          // from the probe: Studio's own flags for the open video
  function rowIsRed(r) {
    for (const e of r.row.querySelectorAll('*')) {
      if (e === r.del || r.del.contains(e) || e === r.input) continue;
      const lab = (e.getAttribute && ((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('title') || ''))) || '';
      if (/unlikely|won.?t show|may not show|disrupt/i.test(lab)) return true;
      const tag = e.tagName || '';
      const iconish = ICON_RE.test(tag) || /icon/i.test(typeof e.className === 'string' ? e.className : ((e.className && e.className.baseVal) || ''));
      if (!iconish) continue;
      try {
        const cs = getComputedStyle(e);
        if (cssColorIsRed(cs.color) || cssColorIsRed(cs.fill) || cssColorIsRed(cs.stroke)) return true;
      } catch (_) {}
    }
    const vid = (location.pathname.match(/\/video\/([\w-]{11})/) || [])[1];
    if (r.t != null && disruptive.videoId && disruptive.videoId === vid) {
      const ms = r.t * 1000;
      if (disruptive.ms.some((d) => Math.abs(d - ms) <= 60)) return true;
    }
    return false;
  }
  function slotListScroller() {
    const first = slotRows()[0]; if (!first) return null;
    let n = first.row.parentElement;
    for (let i = 0; n && i < 10; i++, n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 4) return n;
    }
    return null;
  }
  async function removeRedBreaks() {
    if (!adEditorOpen()) { toast('Open the video’s Mid-roll ad slots editor first.'); return; }
    const sc = slotListScroller();
    if (sc) { sc.scrollTop = 0; await sleep(120); }
    let removed = 0, lastRow = null, stuck = 0;
    for (let guard = 0; guard < 600; guard++) {
      const red = slotRows().find(rowIsRed);
      if (red) {
        if (red.row === lastRow && ++stuck > 2) break;   // the trash didn't take: stop instead of looping
        if (red.row !== lastRow) stuck = 0;
        lastRow = red.row;
        red.del.click(); removed++;
        setStatus('Removing red slots… ' + removed);
        await sleep(120);
        continue;
      }
      if (sc && sc.scrollTop + sc.clientHeight < sc.scrollHeight - 2) {  // long lists render as you scroll
        sc.scrollTop += Math.max(60, sc.clientHeight - 40);
        await sleep(180);
        continue;
      }
      break;
    }
    if (sc) sc.scrollTop = 0;
    if (removed) { setStatus('Removed ' + removed + ' red slot' + (removed === 1 ? '' : 's') + '. Review, then Save.'); toast('Removed ' + removed + (removed === 1 ? ' slot that was' : ' slots that were') + ' unlikely to show ads. Nothing is saved until you press Save.'); return; }
    toast('No slots are flagged as unlikely to show ads right now.');
    if (slotRows().length === 0) sendDiag('ad-rows-not-found');
  }

  // When the editor doesn't look like we expect, send its STRUCTURE (tags, classes, labels and
  // slot times — no other page text) to the site so the selectors can be fixed without a
  // copy-paste round trip. Token-gated, at most once per page load per reason.
  const diagSent = new Set();
  function skeleton(node, depth) {
    if (!node || depth > 14) return '';
    const pad = '  '.repeat(depth);
    const tag = (node.tagName || '').toLowerCase();
    const cls = typeof node.className === 'string' ? node.className.trim().split(/\s+/).slice(0, 4).join('.') : '';
    const bits = [];
    for (const a of ['id', 'role', 'aria-label', 'title', 'icon', 'type']) { const v = node.getAttribute && node.getAttribute(a); if (v) bits.push(a + '="' + String(v).slice(0, 60) + '"'); }
    if (tag === 'input') bits.push('value="' + String(node.value || '').slice(0, 20) + '"');
    let color = '';
    try { const cs = getComputedStyle(node); if (cssColorIsRed(cs.color) || cssColorIsRed(cs.fill)) color = ' RED'; } catch (_) {}
    let out = pad + '<' + tag + (cls ? '.' + cls : '') + (bits.length ? ' ' + bits.join(' ') : '') + '>' + color + '\n';
    for (const c of node.children || []) out += skeleton(c, depth + 1);
    return out;
  }
  function sendDiag(reason) {
    if (diagSent.has(reason)) return; diagSent.add(reason);
    try {
      const ins = deepestButton(INSERT_RE);
      let region = ins; for (let i = 0; region && i < 8; i++) region = region.parentElement;
      const text = skeleton(region || document.body, 0).slice(0, 120000);
      fetch(site() + '/api/ext/markup', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-SF-Token': settings.token || '' },
        body: JSON.stringify({ label: reason, markup: text }) }).catch(() => {});
    } catch (_) {}
  }
  // Studio's own ad settings for the open video, relayed by the MAIN-world probe
  window.addEventListener('message', (e) => {
    try {
      if (e.source !== window || !e.data || e.data.source !== 'sf-adbreaks') return;
      disruptive = { videoId: String(e.data.videoId || ''), ms: (e.data.disruptiveMs || []).map(Number).filter(isFinite) };
    } catch (_) {}
  });

  /* ---- "Latest video performance" (Studio dashboard): rank vs ALL uploads + engaged views ----
     The numbers come from the page probe (studio_probe_main.js), which asks Studio with its own
     session; this part only adds two rows to the card, cloned from the card's own rows so they
     look native: "Ranking vs all videos  37 of 251" under "Ranking by views", and
     "Engaged views  6" under "Views". */
  let latestInfo = null;
  window.addEventListener('message', (e) => {
    try {
      if (e.source !== window || !e.data || e.data.source !== 'sf-latest') return;
      latestInfo = e.data;
      safe('latest', renderLatest);
    } catch (_) { /* ignore */ }
  });
  function textEl(root, re) {                  // the element that holds a text node matching `re`
    if (!root) return null;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) if (re.test(n.nodeValue || '')) return n.parentElement;
    return null;
  }
  function rowFor(labelEl, card, valueRe) {    // climb from a label to the row that also holds its value
    let n = labelEl;
    for (let i = 0; n && n !== card && i < 6; i++, n = n.parentElement) {
      if (valueRe.test((n.textContent || '').replace(/\s+/g, ' ').trim())) return n;
    }
    return null;
  }
  function textLeaves(root) {
    const out = [];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) if ((n.nodeValue || '').trim()) out.push(n);
    return out;
  }
  function fmtSpan(s) {
    s = Math.max(0, Math.round(s || 0));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d) return d + ' day' + (d === 1 ? '' : 's') + (h ? ' ' + h + ' h' : '');
    return (h ? h + ' h ' : '') + m + ' min';
  }
  function latestRank(info) {
    if (info.rank != null) {
      const window_ = info.mode === 'exact' ? 'the same first ' + fmtSpan(info.spanS) : 'their first ' + info.spanDays + ' day' + (info.spanDays === 1 ? '' : 's');
      return { value: info.rank + ' of ' + info.of,
        tip: 'Where this video ranks by views against ALL ' + info.of + ' of your public ' + (info.shorts ? 'Shorts' : 'videos') + ', each measured over ' + window_ + '. Studio’s own line only compares the last 10.' };
    }
    if (info.rankUnavailable === 'first-day') return { value: 'after 24 h', tip: 'YouTube keeps hour-by-hour history only for recent videos, so the ranking against all your videos starts once this one is a day old.' };
    if (info.rankUnavailable === 'no-peers') return { value: '1 of 1', tip: 'This is your first public video in this format.' };
    if (info.rankError || info.rankUnavailable) return { value: '—', tip: 'Could not compare against all videos: ' + (info.rankError || info.rankUnavailable) };
    return { value: '…', tip: 'Comparing against all your videos…' };
  }
  function latestEngaged(info) {
    if (typeof info.engaged === 'number') return { value: Number(info.engaged).toLocaleString(), tip: 'Engaged views since publish: the stricter count YouTube used before Aug 2026 and still pays on. Views count every playback start.' };
    if (info.engagedError) return { value: '—', tip: 'Could not read engaged views: ' + info.engagedError };
    return { value: '…', tip: 'Reading engaged views…' };
  }
  function putRow(card, key, after, label, v) {
    let row = card.querySelector('.sf-latest-row[data-k="' + key + '"]');
    if (!row) {
      if (!after) return;
      row = after.cloneNode(true);
      row.querySelectorAll('a, button, [role="button"], tp-yt-paper-icon-button, ytcp-icon-button, yt-icon, tp-yt-iron-icon, svg, img').forEach((n) => n.remove());
      if (textLeaves(row).length < 2) {        // an unexpected layout: our own plain row instead
        row = el('div', 'sf-latest-simple');
        row.appendChild(el('span')); row.appendChild(el('span'));
      }
      row.classList.add('sf-latest-row'); row.dataset.k = key;
      after.insertAdjacentElement('afterend', row);
    }
    const leaves = textLeaves(row);
    if (row.classList.contains('sf-latest-simple')) { row.children[0].textContent = label; row.children[1].textContent = v.value; }
    else if (leaves.length >= 2) {
      leaves[0].nodeValue = label; leaves[leaves.length - 1].nodeValue = v.value;
      for (let i = 1; i < leaves.length - 1; i++) leaves[i].nodeValue = '';
    }
    row.title = v.tip;
  }
  function renderLatest() {
    const info = latestInfo;
    const stale = document.querySelectorAll('.sf-latest-row');
    if (settings.studioLatestPlus === false || !info) { stale.forEach((r) => r.remove()); return; }
    const title = textEl(document.body, /^\s*latest video performance\s*$/i);
    if (!title) return;
    let card = title;
    for (let i = 0; card && i < 10 && !/ranking by views/i.test(card.textContent || ''); i++) card = card.parentElement;
    if (!card) return;
    // only draw on the card of the same video (a channel switch loads another dashboard)
    const ids = [...card.querySelectorAll('a[href*="/video/"], img[src*="/vi/"]')]
      .map((n) => ((n.getAttribute('href') || n.getAttribute('src') || '').match(/\/(?:video|vi)\/([\w-]{11})/) || [])[1]).filter(Boolean);
    if (ids.length && !ids.includes(info.videoId)) { card.querySelectorAll('.sf-latest-row').forEach((r) => r.remove()); return; }
    const rankLbl = textEl(card, /^\s*ranking by views\s*$/i);
    const rankRow = rankLbl && rowFor(rankLbl, card, /ranking by views\s*\d[\d,]*\s*of\s*\d/i);
    const viewsLbl = textEl(card, /^\s*views\s*$/i);
    const viewsRow = viewsLbl && rowFor(viewsLbl, card, /^views\s*[\d.,]+\s*[KMB]?\b/i);
    putRow(card, 'rank', rankRow, 'Ranking vs all videos', latestRank(info));
    putRow(card, 'engaged', viewsRow, 'Engaged views', latestEngaged(info));
  }

  /* ================================================================ engine */
  // throttle (not debounce): Studio's DOM changes constantly while a video plays in the editor,
  // and a debounce that restarts on every change could keep the Ad Placer from ever appearing
  const runAll = SF.throttle(() => safe('runAll', () => { ensureFab(); scanExplainers(); scanOutliers(); ensureAdCard(); renderLatest(); }), 400);
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
