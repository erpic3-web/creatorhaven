/* CreatorHaven — content/yt.js  (www.youtube.com, isolated world, classic script, document_start)
 *
 * Every feature here is:
 *   - gated by its settings toggle (read from storage.sync, live-updated via onChanged),
 *   - idempotent: it marks what it touched with data-sf-* attributes / sf-* classes and
 *     skips on the next pass, so the MutationObserver loop converges,
 *   - re-run on a debounced MutationObserver + YouTube's SPA navigation events + a 1 s
 *     URL poll (belt and braces — YouTube renames its events every so often),
 *   - wrapped in try/catch: one broken selector logs "[SF] <feature>" once and never
 *     takes the other features (or YouTube) down.
 *
 * YouTube desktop renders its Polymer elements into the light DOM ("shady" DOM), which is
 * why plain querySelector works here; Studio (content/studio.js) needs shadow-root walking.
 */
(() => {
  'use strict';

  const api = globalThis.browser ?? globalThis.chrome;
  const SF = globalThis.SF;
  const TAG = '[SF]';
  if (!SF || !api || !api.runtime || !api.storage) {
    console.warn(TAG, 'yt.js: runtime or lib/common.js missing, nothing to do');
    return;
  }

  /* ================================================================ infrastructure */

  let settings = SF.withDefaults({});
  let favoritesList = [];
  const warned = new Set();
  const state = {
    plQuery: '',              // playlist search text
    namesPausedUntil: 0,      // realNames: back off after a site failure
    namesInFlight: false,
    namesWarned: false,
    lastHref: location.href
  };

  function warn(scope, err) {
    const key = scope + ':' + (err && err.message ? err.message : String(err));
    if (warned.has(key)) return;
    warned.add(key);
    console.warn(TAG, scope, err);
  }

  function safe(scope, fn) {
    try { return fn(); } catch (e) { warn(scope, e); return undefined; }
  }

  // runtime.sendMessage as a promise in both Chrome (MV3) and Firefox (browser.*).
  function send(msg) {
    try {
      const ret = api.runtime.sendMessage(msg);
      if (ret && typeof ret.then === 'function') {
        return ret.then(r => r || { ok: false, error: 'empty response' })
                  .catch(e => ({ ok: false, error: String((e && e.message) || e) }));
      }
      return Promise.resolve({ ok: false, error: 'sendMessage returned no promise' });
    } catch (e) {
      return Promise.resolve({ ok: false, error: String((e && e.message) || e) });
    }
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  let toastTimer = 0;
  function toast(text) {
    safe('toast', () => {
      if (!document.body) return;
      let t = document.querySelector('.sf-toast');
      if (!t) { t = el('div', 'sf-toast'); document.body.appendChild(t); }
      t.textContent = text;
      t.classList.add('sf-toast-show');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => t.classList.remove('sf-toast-show'), 2200);
    });
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* fall through */ }
    try {
      const ta = el('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;opacity:0;left:-9999px';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch (_) { return false; }
  }

  const page = {
    videoId: () => SF.parseVideoId(location.href),
    isWatch: () => location.pathname === '/watch',
    isPlaylist: () => location.pathname === '/playlist',
    isShorts: () => location.pathname.startsWith('/shorts/')
  };

  // YouTube writes <head> microdata (itemprop=identifier, canonical link, og:video:tag,
  // datePublished) for the FIRST video loaded; after SPA navigation it can lag behind.
  // We refuse stale metadata rather than show a wrong date / tag set for the new video.
  function metaMatches(id) {
    const ident = document.querySelector('meta[itemprop="identifier"]');
    if (ident && ident.content && ident.content !== id) return false;
    const canon = document.querySelector('link[rel="canonical"]');
    if (canon && canon.href) {
      const cid = SF.parseVideoId(canon.href);
      if (cid && cid !== id) return false;
    }
    return true;
  }

  /* ================================================================ features */

  const features = [];
  const active = new Map();
  function feature(def) { features.push(def); }

  /* ---- 1. confirmSignOut -------------------------------------------------- */
  // Capture-phase click guard. composedPath() gives the real click path even through
  // shadow roots (YouTube's account menu uses some) and lets us stop at <body>.
  function isSignOutTarget(path) {
    for (const node of path) {
      if (!node || node.nodeType !== 1) continue;
      if (node === document.body) return false;
      if (node.tagName === 'A' && /\/logout(\b|$)/.test(node.getAttribute('href') || '')) return true;
      if (node.matches && node.matches('a, button, [role="menuitem"], [role="link"], tp-yt-paper-item, ytd-compact-link-renderer, yt-formatted-string')) {
        const t = (node.textContent || '').trim();
        if (t.length <= 12 && /^sign out$/i.test(t)) return true;
      }
    }
    return false;
  }

  function installSignOutGuard() {
    document.addEventListener('click', e => {
      if (!settings.confirmSignOut) return;
      safe('confirmSignOut', () => {
        const path = typeof e.composedPath === 'function' ? e.composedPath() : [e.target];
        if (!isSignOutTarget(path)) return;
        if (!window.confirm('Sign out of all Google accounts?')) {
          e.preventDefault();
          e.stopImmediatePropagation();
        }
      });
    }, true);
  }

  /* ---- 2. exactDates ------------------------------------------------------ */
  // -> { text, iso } or null.  Exact time comes from <head> microdata / the inline player
  // response (first page load). After SPA navigation those can be stale, so the fallback is
  // the tooltip YouTube renders on the info line for the CURRENT video
  // ("3,215 views • Sep 8, 2026") — date only, but never wrong.
  function readPublishInfo(id) {
    if (metaMatches(id)) {
      let iso = null;
      const m1 = document.querySelector('meta[itemprop="datePublished"]');
      const m2 = document.querySelector('meta[itemprop="uploadDate"]');
      if (m1 && m1.content) iso = m1.content;
      else if (m2 && m2.content) iso = m2.content;
      else {
        for (const s of document.scripts) {
          const t = s.textContent;
          if (!t || t.length < 100 || !t.includes('"publishDate"')) continue;
          if (!t.includes('"videoId":"' + id + '"')) continue;   // only a script about THIS video
          const d = SF.extractPublishDate(t);
          if (d) { iso = d; break; }
        }
      }
      if (iso) {
        const text = SF.formatExactDate(iso);
        if (text) return { text, iso };
      }
    }
    const tip = document.querySelector('ytd-watch-info-text #tooltip, ytd-watch-info-text tp-yt-paper-tooltip');
    const m = tip && (tip.textContent || '').match(/\b([A-Z][a-z]{2,8} \d{1,2}, \d{4})\b/);
    if (m) return { text: m[1], iso: '' };
    return null;
  }

  // Text nodes of the watch-info area only (never comments, never the description body).
  const DATE_ROOTS = ['ytd-watch-metadata ytd-watch-info-text', 'ytd-watch-info-text', 'ytd-watch-metadata #info', '#info-strings', 'ytd-video-primary-info-renderer #info-text'];
  const DATE_SKIP = 'yt-attributed-string, #expanded, #snippet-text, #plain-snippet-text, .sf-exact-date, ytd-comments, #comments';

  function findRelativeDateNode() {
    for (const sel of DATE_ROOTS) {
      for (const root of document.querySelectorAll(sel)) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
          const text = node.nodeValue;
          if (!text || !SF.relativeDateRegex.test(text)) continue;
          const parent = node.parentElement;
          if (!parent || parent.closest(DATE_SKIP)) continue;
          return node;
        }
      }
    }
    return null;
  }

  feature({
    name: 'exactDates', key: 'exactDates',
    run() {
      if (!page.isWatch()) return;
      const id = page.videoId();
      if (!id) return;
      const marked = document.querySelector('[data-sf-dated="' + id + '"]');
      if (marked) {
        if (marked.querySelector('.sf-exact-date')) return;   // done
        marked.removeAttribute('data-sf-dated');               // YouTube re-rendered the text: redo
      }
      for (const stale of document.querySelectorAll('[data-sf-dated]')) {
        if (stale.getAttribute('data-sf-dated') !== id) {
          stale.removeAttribute('data-sf-dated');
          for (const s of stale.querySelectorAll('.sf-exact-date')) s.remove();
        }
      }
      const info = readPublishInfo(id);
      if (!info) return;
      const node = findRelativeDateNode();
      if (!node) return;
      const span = el('span', 'sf-exact-date', ' · ' + info.text);
      span.title = info.iso ? info.iso : 'exact time unavailable after in-page navigation — reload for the time';
      node.parentNode.insertBefore(span, node.nextSibling);
      node.parentElement.setAttribute('data-sf-dated', id);
    },
    off() {
      for (const s of document.querySelectorAll('.sf-exact-date')) s.remove();
      for (const m of document.querySelectorAll('[data-sf-dated]')) m.removeAttribute('data-sf-dated');
    }
  });

  /* ---- 3. videoTags ------------------------------------------------------- */
  feature({
    name: 'videoTags', key: 'videoTags',
    run() {
      const existing = document.querySelector('.sf-tags');
      if (!page.isWatch()) { if (existing) existing.remove(); return; }
      const id = page.videoId();
      if (!id) return;
      if (existing && existing.dataset.sfVideo === id && existing.isConnected) return;
      if (existing) existing.remove();
      if (!metaMatches(id)) return;
      const tags = Array.from(document.querySelectorAll('meta[property="og:video:tag"]'))
        .map(m => (m.content || '').trim()).filter(Boolean);
      if (!tags.length) return;
      const wm = document.querySelector('ytd-watch-metadata');
      const bottom = wm && wm.querySelector('#bottom-row');
      const anchor = bottom || wm || document.querySelector('#primary-inner');
      if (!anchor) return;
      const row = el('div', 'sf-tags');
      row.dataset.sfVideo = id;
      row.appendChild(el('span', 'sf-tags-label', 'Tags (' + tags.length + '):'));
      for (const tag of tags) {
        const chip = el('button', 'sf-chip', tag);
        chip.type = 'button';
        chip.title = 'Copy tag';
        chip.addEventListener('click', ev => {
          ev.preventDefault(); ev.stopPropagation();
          copyText(tag).then(ok => toast(ok ? 'Copied: ' + tag : 'Copy failed'));
        });
        row.appendChild(chip);
      }
      if (bottom) bottom.insertAdjacentElement('afterend', row);
      else anchor.appendChild(row);
    },
    off() { for (const r of document.querySelectorAll('.sf-tags')) r.remove(); }
  });

  /* ---- 4. thumbDownloader ------------------------------------------------- */
  // Lives in the like/share row when it exists; otherwise a fixed floating pill.
  // No "downloads" permission: the best image URL opens in a new tab (+ copy URL).
  async function openBestThumb(copyOnly) {
    const id = page.videoId();
    if (!id) { toast('Not on a video'); return; }
    const r = await send({ type: 'thumbBest', id });
    if (!r.ok) { toast('No thumbnail found'); return; }
    if (copyOnly) { toast((await copyText(r.url)) ? 'Thumbnail URL copied' : 'Copy failed'); return; }
    const w = window.open(r.url, '_blank', 'noopener');
    if (!w) { await copyText(r.url); toast('Popup blocked — URL copied instead'); }
    else if (r.placeholder) toast('Only a low-res thumbnail exists for this video');
  }

  feature({
    name: 'thumbDownloader', key: 'thumbDownloader',
    run() {
      const existing = document.querySelector('.sf-thumb-wrap');
      if (!page.isWatch()) { if (existing) existing.remove(); return; }
      const host = document.querySelector('ytd-watch-metadata #top-level-buttons-computed')
        || document.querySelector('ytd-watch-metadata #actions-inner #menu')
        || document.querySelector('ytd-watch-metadata #actions');
      if (existing && existing.isConnected) {
        // Move out of floating mode once the real row shows up.
        if (host && existing.classList.contains('sf-floating') && !host.contains(existing)) {
          existing.classList.remove('sf-floating'); host.appendChild(existing);
        }
        return;
      }
      if (existing) existing.remove();
      if (!document.body) return;
      const wrap = el('div', 'sf-thumb-wrap');
      const main = el('button', 'sf-thumb-btn');
      main.type = 'button'; main.title = 'Open the best available thumbnail in a new tab';
      main.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5.5" width="17" height="13" rx="1"/><path d="m3.5 15.5 4.5-4.5 4 4 3-3 5.5 5.5"/><circle cx="15.5" cy="9.5" r="1.5"/></svg><span>Thumbnail</span>';
      const copy = el('button', 'sf-thumb-copy');
      copy.type = 'button'; copy.title = 'Copy thumbnail URL'; copy.setAttribute('aria-label', 'Copy thumbnail URL');
      copy.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="1"/><path d="M15 9V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h4"/></svg>';
      main.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); openBestThumb(false); });
      copy.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); openBestThumb(true); });
      wrap.append(main, copy);
      if (host) host.appendChild(wrap);
      else { wrap.classList.add('sf-floating'); document.body.appendChild(wrap); }
    },
    off() { for (const w of document.querySelectorAll('.sf-thumb-wrap')) w.remove(); }
  });

  /* ---- 5. playlistSearch -------------------------------------------------- */
  function applyPlaylistFilter() {
    const q = state.plQuery.trim().toLowerCase();
    const rows = document.querySelectorAll('ytd-playlist-video-renderer');
    let shown = 0;
    for (const row of rows) {
      const titleEl = row.querySelector('#video-title');
      const title = ((titleEl && (titleEl.getAttribute('title') || titleEl.textContent)) || '').trim().toLowerCase();
      const hit = !q || title.includes(q);
      if (hit) { shown++; row.removeAttribute('data-sf-pl-hidden'); }
      else row.setAttribute('data-sf-pl-hidden', '1');
    }
    const count = document.querySelector('.sf-pl-count');
    if (count) count.textContent = q ? shown + ' / ' + rows.length : rows.length + ' loaded';
  }

  feature({
    name: 'playlistSearch', key: 'playlistSearch',
    run() {
      const existing = document.querySelector('.sf-pl-search');
      if (!page.isPlaylist()) { if (existing) existing.remove(); return; }
      const list = document.querySelector('ytd-playlist-video-list-renderer');
      if (!list) return;
      if (!existing || !existing.isConnected) {
        if (existing) existing.remove();
        const box = el('div', 'sf-pl-search');
        const input = el('input');
        input.type = 'search'; input.placeholder = 'Search this playlist by title…';
        input.value = state.plQuery;
        input.setAttribute('aria-label', 'Search this playlist');
        input.addEventListener('input', () => { state.plQuery = input.value; applyPlaylistFilter(); });
        input.addEventListener('keydown', ev => { ev.stopPropagation(); }); // keep YouTube hotkeys out
        box.append(input, el('span', 'sf-pl-count', ''));
        list.parentNode.insertBefore(box, list);
      }
      applyPlaylistFilter();   // re-applies after lazy loads (observer pass)
    },
    off() {
      for (const b of document.querySelectorAll('.sf-pl-search')) b.remove();
      for (const r of document.querySelectorAll('[data-sf-pl-hidden]')) r.removeAttribute('data-sf-pl-hidden');
      state.plQuery = '';
    }
  });

  /* ---- 6. feedCleaner ----------------------------------------------------- */
  // html.sf-feed-clean + yt.css hide the structurally identifiable bits; JS marks the
  // shelves that can only be told apart by their header text.
  function shelfHeaderText(shelf) {
    const h = shelf.querySelector('#rich-shelf-header #title, #title-text, #title, h2, #header .title, yt-formatted-string#title');
    return h ? (h.textContent || '').trim() : '';
  }

  feature({
    name: 'feedCleaner', key: 'feedCleaner',
    run() {
      document.documentElement.classList.add('sf-feed-clean');
      const shelves = document.querySelectorAll('ytd-rich-shelf-renderer:not([data-sf-shelf]), ytd-rich-section-renderer:not([data-sf-shelf]), ytd-shelf-renderer:not([data-sf-shelf]), ytd-reel-shelf-renderer:not([data-sf-shelf])');
      for (const shelf of shelves) {
        if (shelf.tagName === 'YTD-REEL-SHELF-RENDERER' || shelf.hasAttribute('is-shorts')) { shelf.setAttribute('data-sf-shelf', 'hide'); continue; }
        const text = shelfHeaderText(shelf);
        if (!text) continue;                     // header not rendered yet: look again next pass
        shelf.setAttribute('data-sf-shelf', SF.shelfBlockRegex.test(text) ? 'hide' : 'keep');
      }
      const guides = document.querySelectorAll('ytd-guide-entry-renderer:not([data-sf-shelf]), ytd-mini-guide-entry-renderer:not([data-sf-shelf])');
      for (const g of guides) {
        const a = g.querySelector('a');
        const label = ((a && (a.getAttribute('title') || a.getAttribute('aria-label'))) || (g.textContent || '')).trim();
        if (!label) continue;
        g.setAttribute('data-sf-shelf', /^shorts$/i.test(label) ? 'hide' : 'keep');
      }
    },
    off() {
      document.documentElement.classList.remove('sf-feed-clean');
      for (const s of document.querySelectorAll('[data-sf-shelf]')) s.removeAttribute('data-sf-shelf');
    }
  });

  /* ---- 7. shortsRedirect -------------------------------------------------- */
  function maybeRedirectShorts() {
    if (!settings.shortsRedirect || !page.isShorts()) return;
    const id = page.videoId();
    if (id) location.replace('/watch?v=' + id);
  }
  feature({ name: 'shortsRedirect', key: 'shortsRedirect', run: maybeRedirectShorts });

  /* ---- 8. realNames + subCounts ------------------------------------------- */
  // Handle text (e.g. "@someone") in comments and channel-name slots -> display name via
  // the site (cached 7 days in storage.local under hn:<handle>), plus "· 1.2M" if subCounts.
  const HANDLE_SLOTS = 'ytd-comment-view-model #author-text, ytd-comment-renderer #author-text, ytd-channel-name #text';

  function slotTextEl(slot) {
    // The visible text sits in the deepest element that holds it (a <span> inside <a>).
    let cur = slot;
    for (let i = 0; i < 4; i++) {
      const kids = Array.from(cur.children).filter(c => !c.classList.contains('sf-subs') && (c.textContent || '').trim());
      if (kids.length !== 1) break;
      cur = kids[0];
    }
    return cur;
  }

  function applyName(slot, handle, info) {
    slot.setAttribute('data-sf-handle', handle);
    // "miss" = the site does not know this handle (negative-cached): leave it alone.
    slot.setAttribute('data-sf-name', info && info.title ? 'ok' : 'miss');
    if (!info || !info.title) return;
    if (settings.realNames) {
      const textEl = slotTextEl(slot);
      if ((textEl.textContent || '').trim() !== info.title) textEl.textContent = info.title;
      slot.setAttribute('title', handle);
    }
    if (settings.subCounts && info.subscribers != null && Number.isFinite(Number(info.subscribers))) {
      let badge = slot.querySelector('.sf-subs');
      if (!badge) { badge = el('span', 'sf-subs'); slot.appendChild(badge); }
      const txt = ' · ' + SF.compactNumber(info.subscribers);
      if (badge.textContent !== txt) badge.textContent = txt;
      badge.title = SF.compactNumber(info.subscribers) + ' subscribers';
    }
  }

  async function resolveHandles(slots) {
    const byHandle = new Map();
    for (const { slot, handle } of slots) {
      if (!byHandle.has(handle)) byHandle.set(handle, []);
      byHandle.get(handle).push(slot);
    }
    const keys = Array.from(byHandle.keys()).map(SF.handleKey);
    let cached = {};
    try { cached = await api.storage.local.get(keys); } catch (e) { warn('realNames:cache', e); }
    const now = Date.now();
    const missing = [];
    for (const handle of byHandle.keys()) {
      const row = cached[SF.handleKey(handle)];
      const ttl = row && row.title ? SF.HANDLE_CACHE_TTL_MS : SF.HANDLE_NEG_TTL_MS;
      if (row && row.ts && now - row.ts < ttl) {
        for (const slot of byHandle.get(handle)) applyName(slot, handle, row);
      } else missing.push(handle);
    }
    if (!missing.length) return;
    if (!settings.token) { if (!state.namesWarned) { state.namesWarned = true; console.info(TAG, 'realNames: no site token set — showing cached names only'); } return; }
    if (now < state.namesPausedUntil) return;
    if (state.namesInFlight) return;
    state.namesInFlight = true;
    try {
      for (const batch of SF.chunk(missing, SF.MAX_HANDLES_PER_REQUEST)) {
        const r = await send({ type: 'channels', handles: batch });
        if (!r.ok) {
          state.namesPausedUntil = Date.now() + 5 * 60 * 1000;
          if (!state.namesWarned) { state.namesWarned = true; console.info(TAG, 'realNames: site unreachable, pausing 5 min —', r.error); }
          return;
        }
        const map = {};
        for (const [k, v] of Object.entries(r.channels || {})) map[SF.normalizeHandle(k).toLowerCase()] = v;
        const write = {};
        for (const handle of batch) {
          const info = map[handle.toLowerCase()];
          const row = info
            ? { title: info.title || '', subscribers: info.subscribers == null ? null : Number(info.subscribers), id: info.id || '', ts: Date.now() }
            : { title: '', subscribers: null, ts: Date.now() };
          write[SF.handleKey(handle)] = row;
          for (const slot of byHandle.get(handle) || []) applyName(slot, handle, row);
        }
        try { await api.storage.local.set(write); } catch (e) { warn('realNames:cacheWrite', e); }
      }
    } finally {
      state.namesInFlight = false;
    }
  }

  feature({
    name: 'realNames', key: 'realNames', altKey: 'subCounts',
    run() {
      const pending = [];
      for (const slot of document.querySelectorAll(HANDLE_SLOTS)) {
        const text = (slotTextEl(slot).textContent || '').trim();
        if (!text.startsWith('@')) continue;
        if (slot.hasAttribute('data-sf-handle')) {
          // Already handled. Only look again when realNames is on and YouTube re-rendered
          // the raw handle over our display name (negative-cached slots stay as they are).
          if (slot.getAttribute('data-sf-name') === 'miss' || !settings.realNames) continue;
        }
        const handle = SF.normalizeHandle(text.split(/\s+/)[0]);
        if (!handle) continue;
        pending.push({ slot, handle });
      }
      if (pending.length) resolveHandles(pending).catch(e => warn('realNames', e));
    },
    off() {
      for (const slot of document.querySelectorAll('[data-sf-handle]')) {
        const handle = slot.getAttribute('data-sf-handle');
        const badge = slot.querySelector('.sf-subs');
        if (badge) badge.remove();
        if (handle && slot.getAttribute('data-sf-name') === 'ok') slotTextEl(slot).textContent = handle;
        slot.removeAttribute('data-sf-handle');
        slot.removeAttribute('data-sf-name');
        slot.removeAttribute('title');
      }
    }
  });

  /* ---- 9. dontRecommend --------------------------------------------------- */
  // an x chip on hover of a home-feed card -> opens the card's own overflow menu and clicks
  // "Don't recommend channel" for the user (YouTube keeps that action three clicks deep).
  function findMenuItem(re) {
    const candidates = document.querySelectorAll('ytd-menu-service-item-renderer, tp-yt-paper-item, [role="menuitem"], yt-list-item-view-model');
    for (const c of candidates) {
      const t = (c.textContent || '').trim();
      if (t.length < 60 && re.test(t)) return c;
    }
    return null;
  }

  function closeOpenMenu() {
    const backdrop = document.querySelector('tp-yt-iron-overlay-backdrop');
    if (backdrop) backdrop.click();
    const target = document.activeElement || document.body;
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true }));
  }

  async function dontRecommendFrom(item) {
    const menuBtn = item.querySelector('ytd-menu-renderer yt-icon-button#button button, ytd-menu-renderer #button button, ytd-menu-renderer button[aria-label], button[aria-label="Action menu"], button[aria-label="More actions"]');
    if (!menuBtn) { toast('Not available here'); return; }
    menuBtn.click();
    const deadline = Date.now() + 1500;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 100));
      const hit = findMenuItem(/don.t recommend channel/i);
      if (hit) { hit.click(); toast('Channel hidden from recommendations'); return; }
    }
    closeOpenMenu();
    toast('Not available here');
  }

  function installDnrHover() {
    document.addEventListener('mouseover', e => {
      if (!settings.dontRecommend) return;
      safe('dontRecommend', () => {
        const t = e.target;
        if (!t || !t.closest) return;
        const item = t.closest('ytd-rich-item-renderer');
        if (!item || item.querySelector(':scope > .sf-dnr')) return;
        const btn = el('button', 'sf-dnr', '✕');
        btn.type = 'button'; btn.title = "Don't recommend this channel";
        btn.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); dontRecommendFrom(item).catch(err => warn('dontRecommend', err)); });
        item.appendChild(btn);
      });
    }, true);
  }

  feature({
    name: 'dontRecommend', key: 'dontRecommend',
    run() { document.documentElement.classList.add('sf-dnr'); },
    off() {
      document.documentElement.classList.remove('sf-dnr');
      for (const b of document.querySelectorAll('.sf-dnr')) b.remove();
    }
  });

  /* ---- 10. favorites ------------------------------------------------------ */
  function isChannelHref(href) { return /^\/(@|channel\/|c\/|user\/)/.test(href || ''); }
  function favIndex(href) { return favoritesList.findIndex(f => f && f.href === href); }

  async function toggleFavorite(entry) {
    const list = favoritesList.slice();
    const i = favIndex(entry.href);
    if (i >= 0) list.splice(i, 1); else list.push({ title: entry.title, href: entry.href });
    favoritesList = list;
    try { await api.storage.sync.set({ favoritesList: list }); } catch (e) { warn('favorites:save', e); }
    scheduleRun();
  }

  function findSubscriptionsSection() {
    const sections = document.querySelectorAll('ytd-guide-section-renderer');
    for (const sec of sections) {
      const h = sec.querySelector('h3, #guide-section-title');
      if (h && /subscriptions/i.test(h.textContent || '')) return sec;
    }
    for (const sec of sections) if (sec.querySelector('#expandable-items')) return sec;
    return null;
  }

  function renderFavoritesBlock(section) {
    let block = document.querySelector('.sf-favorites');
    if (!block) {
      block = el('div', 'sf-favorites');
      block.appendChild(el('div', 'sf-favorites-title', 'Favorites'));
      block.appendChild(el('div', 'sf-favorites-list'));
    }
    if (block.parentNode !== section.parentNode || block.nextSibling !== section) section.parentNode.insertBefore(block, section);
    const sig = JSON.stringify(favoritesList.map(f => [f.title, f.href]));
    if (block.dataset.sfSig === sig) return;
    block.dataset.sfSig = sig;
    const list = block.querySelector('.sf-favorites-list');
    list.textContent = '';
    if (!favoritesList.length) { list.appendChild(el('div', 'sf-favorites-empty', 'Star a subscription below to pin it here.')); return; }
    for (const f of favoritesList) {
      if (!f || !isChannelHref(f.href)) continue;
      const row = el('div', 'sf-fav');
      const a = el('a', 'sf-fav-link', f.title || f.href);
      a.href = f.href; a.title = f.title || f.href;
      const rm = el('button', 'sf-fav-remove', '✕');
      rm.type = 'button'; rm.title = 'Remove from favorites';
      rm.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); toggleFavorite(f); });
      row.append(a, rm);
      list.appendChild(row);
    }
  }

  feature({
    name: 'favorites', key: 'favorites',
    run() {
      const section = findSubscriptionsSection();
      if (!section) return;
      for (const entry of section.querySelectorAll('ytd-guide-entry-renderer')) {
        const a = entry.querySelector('a#endpoint, a');
        if (!a) continue;
        const href = a.getAttribute('href') || '';
        if (!isChannelHref(href)) continue;
        const title = (a.getAttribute('title') || (entry.querySelector('.title') || a).textContent || '').trim();
        if (!title) continue;
        let star = entry.querySelector(':scope > .sf-star');
        if (!star) {
          star = el('button', 'sf-star');
          star.type = 'button';
          star.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); toggleFavorite({ title, href }); });
          entry.appendChild(star);
        }
        const on = favIndex(href) >= 0;
        star.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3.5 2.6 5.4 5.9.8-4.3 4.1 1.1 5.9-5.3-2.9-5.3 2.9 1.1-5.9-4.3-4.1 5.9-.8z"/></svg>';
        star.title = on ? 'Remove from favorites' : 'Add to favorites';
        star.classList.toggle('sf-star-on', on);
      }
      renderFavoritesBlock(section);
    },
    off() {
      for (const s of document.querySelectorAll('.sf-star')) s.remove();
      for (const b of document.querySelectorAll('.sf-favorites')) b.remove();
    }
  });

  /* ---- 11. appearance ----------------------------------------------------- */
  // Best-effort: YouTube exposes its theme as CSS custom properties on <html>; overriding
  // the accent-related ones recolours buttons/progress/badges, and a font-family override
  // on the text hosts swaps the typeface. Layout tokens vary per YouTube build.
  function appearanceCss() {
    const accent = /^#[0-9a-f]{3,8}$/i.test(settings.appearanceAccent) ? settings.appearanceAccent : SF.SF_DEFAULTS.appearanceAccent;
    const font = String(settings.appearanceFont || '').replace(/[^\w\s,'"-]/g, '').trim();
    const lines = [
      'html, :root, ytd-app {',
      '  --yt-spec-brand-button-background: ' + accent + ' !important;',
      '  --yt-spec-call-to-action: ' + accent + ' !important;',
      '  --yt-spec-static-brand-red: ' + accent + ' !important;',
      '  --yt-spec-red-indicator: ' + accent + ' !important;',
      '  --yt-spec-icon-active-button-link: ' + accent + ' !important;',
      '  --yt-spec-themed-blue: ' + accent + ' !important;',
      '  --yt-spec-brand-icon-active: ' + accent + ' !important;',
      '}',
      '.ytp-play-progress, .ytp-swatch-background-color { background: ' + accent + ' !important; }'
    ];
    if (font) lines.push('html, body, yt-formatted-string, ytd-app, tp-yt-paper-button, button, input, textarea { font-family: ' + font + ', sans-serif !important; }');
    return lines.join('\n');
  }

  feature({
    name: 'appearance', key: 'appearance',
    run() {
      let st = document.getElementById('sf-appearance');
      const css = appearanceCss();
      if (!st) { st = el('style'); st.id = 'sf-appearance'; (document.head || document.documentElement).appendChild(st); }
      if (st.textContent !== css) st.textContent = css;
      for (const grid of document.querySelectorAll('ytd-rich-grid-renderer')) {
        if (settings.appearanceCompact) {
          if (grid.dataset.sfCompact) continue;
          const cur = parseInt(getComputedStyle(grid).getPropertyValue('--ytd-rich-grid-items-per-row'), 10);
          grid.dataset.sfCompact = cur > 0 ? String(cur) : 'x';
          if (cur > 0) grid.style.setProperty('--ytd-rich-grid-items-per-row', String(cur + 1));
          grid.style.setProperty('--ytd-rich-grid-item-margin', '8px');
        } else if (grid.dataset.sfCompact) {
          grid.style.removeProperty('--ytd-rich-grid-items-per-row');
          grid.style.removeProperty('--ytd-rich-grid-item-margin');
          delete grid.dataset.sfCompact;
        }
      }
    },
    off() {
      const st = document.getElementById('sf-appearance');
      if (st) st.remove();
      for (const grid of document.querySelectorAll('ytd-rich-grid-renderer[data-sf-compact]')) {
        grid.style.removeProperty('--ytd-rich-grid-items-per-row');
        grid.style.removeProperty('--ytd-rich-grid-item-margin');
        delete grid.dataset.sfCompact;
      }
    }
  });

  /* ---- 12. sizeCustomizer ------------------------------------------------- */
  // The watch layout sizes the player from two custom properties on <ytd-watch-flexy>.
  // We read the values YouTube computed, remember them, and scale them inline.
  function restorePlayerSize() {
    for (const flexy of document.querySelectorAll('ytd-watch-flexy[data-sf-size-pct]')) {
      flexy.style.removeProperty('--ytd-watch-flexy-max-player-width');
      flexy.style.removeProperty('--ytd-watch-flexy-max-player-height');
      delete flexy.dataset.sfSizePct; delete flexy.dataset.sfOrigW; delete flexy.dataset.sfOrigH;
    }
    const st = document.getElementById('sf-size');
    if (st) st.remove();
  }

  feature({
    name: 'sizeCustomizer', key: 'sizeCustomizer',
    run() {
      const pct = SF.clamp(settings.playerWidthPct, 50, 200);
      const flexy = document.querySelector('ytd-watch-flexy');
      if (flexy && page.isWatch() && flexy.dataset.sfSizePct !== String(pct)) {
        if (!flexy.dataset.sfOrigW) {
          const cs = getComputedStyle(flexy);
          flexy.dataset.sfOrigW = cs.getPropertyValue('--ytd-watch-flexy-max-player-width').trim();
          flexy.dataset.sfOrigH = cs.getPropertyValue('--ytd-watch-flexy-max-player-height').trim();
        }
        const w = parseFloat(flexy.dataset.sfOrigW), h = parseFloat(flexy.dataset.sfOrigH);
        if (w > 0 && h > 0) {
          flexy.style.setProperty('--ytd-watch-flexy-max-player-width', Math.round(w * pct / 100) + 'px');
          flexy.style.setProperty('--ytd-watch-flexy-max-player-height', Math.round(h * pct / 100) + 'px');
          flexy.dataset.sfSizePct = String(pct);
        }
      }
      const px = SF.clamp(settings.commentsFontPx, 8, 40);
      const css = 'ytd-comment-view-model #content-text, ytd-comment-renderer #content-text, ytd-comment-view-model #content-text * { font-size: ' + px + 'px !important; line-height: 1.45 !important; }';
      let st = document.getElementById('sf-size');
      if (!st) { st = el('style'); st.id = 'sf-size'; (document.head || document.documentElement).appendChild(st); }
      if (st.textContent !== css) st.textContent = css;
    },
    off: restorePlayerSize
  });

  /* ================================================================ engine */

  function featureOn(f) {
    return !!settings[f.key] || (f.altKey ? !!settings[f.altKey] : false);
  }

  function runAll() {
    for (const f of features) {
      if (featureOn(f)) {
        safe(f.name, () => f.run && f.run());
        active.set(f.name, true);
      } else if (active.get(f.name)) {
        active.set(f.name, false);
        safe(f.name + ':off', () => f.off && f.off());
      }
    }
  }

  const scheduleRun = SF.debounce(() => safe('runAll', runAll), 300);

  async function loadSettings() {
    try {
      const raw = await api.storage.sync.get(Object.assign({ favoritesList: [] }, SF.SF_DEFAULTS));
      settings = SF.withDefaults(raw);
      favoritesList = Array.isArray(raw.favoritesList) ? raw.favoritesList : [];
    } catch (e) { warn('settings', e); }
  }

  function installSettingsSync() {
    api.storage.onChanged.addListener((changes, area) => {
      if (area !== 'sync') return;
      safe('onChanged', () => {
        const patch = {};
        for (const [k, c] of Object.entries(changes)) {
          if (k === 'favoritesList') { favoritesList = Array.isArray(c.newValue) ? c.newValue : []; continue; }
          patch[k] = c.newValue === undefined ? SF.SF_DEFAULTS[k] : c.newValue;
        }
        settings = SF.withDefaults(Object.assign({}, settings, patch));
        // Force a re-render of things that depend on values, not just toggles.
        if ('appearanceAccent' in patch || 'appearanceFont' in patch || 'appearanceCompact' in patch) { /* run() diffs the css */ }
        if ('playerWidthPct' in patch || 'commentsFontPx' in patch) restorePlayerSize();
        maybeRedirectShorts();
      });
      scheduleRun();
    });
  }

  function onNavigate() {
    state.lastHref = location.href;
    safe('shortsRedirect', maybeRedirectShorts);
    scheduleRun();
  }

  function installNavHooks() {
    for (const ev of ['yt-navigate-finish', 'yt-page-data-updated', 'yt-navigate-start']) {
      window.addEventListener(ev, onNavigate, true);
      document.addEventListener(ev, onNavigate, true);
    }
    setInterval(() => { if (location.href !== state.lastHref) onNavigate(); }, 1000);
  }

  function installObserver() {
    // document_start: <body> may not exist yet, so observe the root element (a superset
    // of body) — every later insertion, including body itself, is caught.
    const mo = new MutationObserver(scheduleRun);
    mo.observe(document.documentElement, { childList: true, subtree: true });
  }

  async function init() {
    installSignOutGuard();
    installDnrHover();
    installSettingsSync();
    await loadSettings();
    safe('shortsRedirect', maybeRedirectShorts);
    installObserver();
    installNavHooks();
    scheduleRun();
  }

  init().catch(e => warn('init', e));
})();
