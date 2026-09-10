/* CreatorHaven — content/thumbgrab.js  (www.youtube.com, isolated world, classic script, document_start)
 *
 * An Ostendo-style on-page thumbnail grabber. A thin steel GRAB tab on the right edge
 * toggles "grab mode": the page dims and every visible video thumbnail is spotlighted above
 * the dim with a subtle outline; hovering one shows Download / Download+title / Copy image.
 *
 * Design (per the feature brief):
 *   - Non-destructive: YouTube's own nodes are never reparented or restyled. We draw our own
 *     absolutely-positioned highlight boxes (each holding a clone <img> of the thumbnail) over
 *     a full-screen dim layer, aligned to each thumbnail's bounding rect, and recompute the
 *     rects on scroll / resize / DOM mutation.
 *   - Fully reversible: toggling off (or Esc, or a click on the dim backdrop) removes the
 *     overlay, boxes and listeners, restoring pointer-events and scrolling.
 *   - Guarded: every entry point is wrapped so one broken selector can never wedge the page.
 *
 * Runs in the same isolated world as content/yt.js (registered on the same content_scripts
 * entry), so globalThis.SF (lib/common.js) is available. It keeps its own IIFE scope.
 */
(() => {
  'use strict';

  const api = globalThis.browser ?? globalThis.chrome;
  const SF = globalThis.SF;
  const TAG = '[SF tg]';
  if (!SF || !api || !api.runtime || !api.storage) {
    console.warn(TAG, 'thumbgrab.js: runtime or lib/common.js missing, nothing to do');
    return;
  }

  /* ================================================================ state */

  let settings = SF.withDefaults({});
  let active = false;                 // grab mode on/off
  let overlay = null;                 // the dim backdrop
  let layer = null;                   // holds the highlight boxes (pointer-events: none)
  let activeObserver = null;          // watches for infinite-scroll thumbnails while active
  const boxes = new Map();            // videoId -> { box, target, title }
  let repositionRaf = 0;
  const warned = new Set();

  function warn(scope, err) {
    const key = scope + ':' + (err && err.message ? err.message : String(err));
    if (warned.has(key)) return;
    warned.add(key);
    console.warn(TAG, scope, err);
  }
  function safe(scope, fn) { try { return fn(); } catch (e) { warn(scope, e); return undefined; } }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // runtime.sendMessage as a promise in both Chrome (MV3) and Firefox.
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

  let toastTimer = 0;
  function toast(text) {
    safe('toast', () => {
      if (!document.body) return;
      let t = document.querySelector('.sf-tg-toast');
      if (!t) { t = el('div', 'sf-tg-toast'); document.body.appendChild(t); }
      t.textContent = text;
      t.classList.add('sf-tg-toast-show');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => t.classList.remove('sf-tg-toast-show'), 2600);
    });
  }

  /* ================================================================ icons (inline SVG, constant) */

  // thin line-art camera (rendered stroke-only via .sf-tg-fab svg { fill:none; stroke:currentColor })
  const SVG_CAMERA = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8.6a1.6 1.6 0 0 1 1.6-1.6h2.5l1.2-1.8h7.4l1.2 1.8h2.5A1.6 1.6 0 0 1 21 8.6v8.9a1.6 1.6 0 0 1-1.6 1.6H4.6A1.6 1.6 0 0 1 3 17.5z"/><circle cx="12" cy="13" r="3.3"/></svg>';
  const SVG_DL     = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19h14"/></svg>';
  const SVG_TAG    = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h5l9 9-6.5 6.5-9-9z"/><circle cx="8.5" cy="8.5" r="1"/></svg>';
  const SVG_COPY   = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="1"/><path d="M15 9V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h4"/></svg>';

  /* ================================================================ thumbnails */

  const ANCHOR_SEL = [
    'a#thumbnail',
    'a.ytd-thumbnail',
    'ytd-thumbnail a',
    'a[href*="watch?v="]',
    'a[href*="/shorts/"]'
  ].join(',');

  const CARD_SEL = 'ytd-rich-item-renderer, ytd-rich-grid-media, ytd-video-renderer, ytd-compact-video-renderer, ytd-grid-video-renderer, ytd-playlist-video-renderer, ytd-playlist-panel-video-renderer, ytd-reel-item-renderer, yt-lockup-view-model, ytd-compact-radio-renderer, ytd-radio-renderer';
  const TITLE_SEL = '#video-title, #video-title-link, yt-formatted-string#video-title, a#video-title-link, .yt-lockup-metadata-view-model__title, h3 a, #title';

  function buildThumbUrl(id, size) {
    return (SF && SF.pickThumbUrl) ? SF.pickThumbUrl(id, size)
      : ('https://i.ytimg.com/vi/' + encodeURIComponent(id) + '/' + size + '.jpg');
  }

  function hrefOf(a) {
    let h = a.getAttribute('href');
    if (!h) { try { h = a.href; } catch (_) { h = ''; } }
    return h || '';
  }

  // The <img> that shows this anchor's thumbnail (inside the anchor, else in the enclosing card).
  function thumbImgFor(a) {
    let img = a.querySelector && a.querySelector('img');
    if (img) return img;
    const card = a.closest && a.closest(CARD_SEL);
    if (card) { img = card.querySelector('a#thumbnail img, ytd-thumbnail img, img'); if (img) return img; }
    return null;
  }

  function findTitle(a) {
    const card = (a.closest && a.closest(CARD_SEL)) || a.parentElement;
    let t = '';
    if (card) {
      const te = card.querySelector(TITLE_SEL);
      if (te) t = (te.getAttribute('title') || te.textContent || '').trim();
    }
    if (!t) t = (a.getAttribute('title') || a.getAttribute('aria-label') || '').trim();
    return t;
  }

  // Channel name, avatar and the "views • age" line of the card, read from the DOM the way a
  // viewer sees it (no class names — YouTube renames them; the channel is the first link to
  // /@handle or /channel/, the avatar is the ggpht image outside the thumbnail, the meta is
  // whatever text lines are left once the title and channel are removed).
  function findMeta(a, title) {
    const card = (a.closest && a.closest(CARD_SEL)) || a.parentElement;
    const out = { channel: '', avatar: '', views: '', age: '' };
    if (!card) return out;
    try {
      const chanA = card.querySelector('a[href^="/@"], a[href^="/channel/"], a[href^="/c/"], a[href^="/user/"], ytd-channel-name a, #channel-name a');
      if (chanA) out.channel = (chanA.textContent || '').replace(/\s+/g, ' ').trim();
      if (!out.channel) {
        const cn = card.querySelector('ytd-channel-name #text, #channel-name, #byline');
        if (cn) out.channel = (cn.textContent || '').replace(/\s+/g, ' ').trim();
      }
      for (const im of card.querySelectorAll('img')) {
        const s = im.currentSrc || im.getAttribute('src') || '';
        if (/ggpht\.com|googleusercontent\.com/.test(s) && !/i\.ytimg\.com/.test(s) && !(a.contains && a.contains(im))) { out.avatar = s; break; }
      }
      const lines = (card.innerText || '').split(/\n+/).map(x => x.trim()).filter(Boolean);
      for (const ln of lines) {
        if (!out.views && /\bviews?\b|\bwatching\b/i.test(ln) && ln.length < 40) out.views = ln;
        else if (!out.age && /\bago\b|^(Streamed|Premiered|Scheduled)/i.test(ln) && ln.length < 40) out.age = ln;
      }
    } catch (_) { /* best effort */ }
    return out;
  }

  // The exact image the user sees when possible (so shorts / cropped variants match), else a
  // reliable constructed URL.
  function displaySrcFor(img, id) {
    const s = (img && (img.currentSrc || img.getAttribute('src'))) || '';
    if (/^https?:\/\//.test(s) && !/^data:|\/1x1|blank|spacer|s\.ytimg\.com/i.test(s)) return s;
    return buildThumbUrl(id, 'hqdefault');
  }

  const VIEW_MARGIN = 900;   // keep boxes for thumbnails within ~a screen of the viewport
  const MIN_W = 100, MIN_H = 56;

  function collectThumbs() {
    const found = new Map();
    const vh = window.innerHeight, vw = window.innerWidth;
    let anchors;
    try { anchors = document.querySelectorAll(ANCHOR_SEL); } catch (_) { return found; }
    for (const a of anchors) {
      const id = SF.parseVideoId(hrefOf(a));
      if (!id) continue;
      const img = thumbImgFor(a);
      if (!img) continue;                                   // text links (comments/desc) have no img
      const rect = img.getBoundingClientRect();
      if (rect.width < MIN_W || rect.height < MIN_H) continue;               // hidden / tiny
      if (rect.bottom < -VIEW_MARGIN || rect.top > vh + VIEW_MARGIN) continue;  // far off-screen
      if (rect.right < 0 || rect.left > vw) continue;                        // off-canvas sidebars
      const area = rect.width * rect.height;
      const prev = found.get(id);
      if (prev && prev.area >= area) continue;              // dedupe: keep the largest instance
      const title = findTitle(a);
      found.set(id, { id, target: img, area, title, meta: findMeta(a, title), src: displaySrcFor(img, id) });
    }
    return found;
  }

  /* ================================================================ image bytes (download / copy) */

  // Try a direct content-script fetch first (works in Firefox and where i.ytimg allows CORS);
  // fall back to the background service worker (host permission, no CORS in extension contexts).
  async function fetchThumbDirect(id) {
    for (const size of ['maxresdefault', 'hqdefault']) {
      let res;
      try { res = await fetch(buildThumbUrl(id, size), { cache: 'no-store' }); }
      catch (_) { return null; }                    // CORS/network -> use the background fallback
      if (!res.ok) continue;                         // 404 for maxres -> try hq
      let blob;
      try { blob = await res.blob(); } catch (_) { return null; }
      if (blob.size < 1500) continue;                // placeholder -> try hq
      return { blob, size, bytes: blob.size };
    }
    return null;
  }

  async function getThumb(id) {
    const direct = await fetchThumbDirect(id);
    if (direct) return direct;
    try {
      const r = await send({ type: 'thumbBlob', id });
      if (r && r.ok && r.dataUrl) {
        const blob = await (await fetch(r.dataUrl)).blob();   // data: URLs are same-origin, no CORS
        return { blob, size: r.size, bytes: r.bytes };
      }
    } catch (e) { warn('getThumb:bg', e); }
    return null;
  }

  // Re-encode the fetched jpg as png via an offscreen canvas (ClipboardItem image support is png).
  // The source is a blob: URL (same-origin) so the canvas is never tainted.
  function jpgToPng(blob) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => {
        try {
          const c = document.createElement('canvas');
          c.width = img.naturalWidth || 1;
          c.height = img.naturalHeight || 1;
          c.getContext('2d').drawImage(img, 0, 0);
          c.toBlob(b => { URL.revokeObjectURL(url); b ? resolve(b) : reject(new Error('toBlob null')); }, 'image/png');
        } catch (e) { URL.revokeObjectURL(url); reject(e); }
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('image decode failed')); };
      img.src = url;
    });
  }

  // Strip characters illegal in Windows filenames, collapse whitespace, cap ~120 chars.
  function sanitizeFilename(name) {
    let s = String(name == null ? '' : name)
      .replace(/[\\/:*?"<>|]/g, '')      // \ / : * ? " < > |
      .replace(/[\u0000-\u001f]/g, ' ')  // control chars
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/[. ]+$/g, '');           // Windows dislikes trailing dots / spaces
    if (s.length > 120) s = s.slice(0, 120).replace(/[. ]+$/g, '').trim();
    return s || 'thumbnail';
  }

  function sizeLabel(size) {
    if (size === 'maxresdefault') return 'maxres 1280×720';
    if (size === 'sddefault') return 'sd 640×480';
    if (size === 'hqdefault') return 'hq 480×360';
    if (size === 'mqdefault') return 'mq 320×180';
    return size || 'thumbnail';
  }

  async function doDownload(id, withTitle) {
    const t = await getThumb(id);
    if (!t) { toast('Thumbnail unavailable'); return; }
    const entry = boxes.get(id);
    const title = (entry && entry.title) || '';
    const base = withTitle ? sanitizeFilename(title || id) : id;
    const name = base + '.jpg';
    try {
      const url = URL.createObjectURL(t.blob);
      const a = el('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => { try { URL.revokeObjectURL(url); } catch (_) { /* ignore */ } }, 8000);
      toast('Saved ' + sizeLabel(t.size) + ' — ' + name);
    } catch (e) { warn('download', e); toast('Download failed'); }
  }

  // Pass a Promise<Blob> to ClipboardItem so the user gesture survives the async fetch (Chrome);
  // fall back to a resolved blob, then to copying the max-res URL as text.
  function pngPromiseFor(id) {
    return (async () => {
      const t = await getThumb(id);
      if (!t) throw new Error('thumbnail unavailable');
      return jpgToPng(t.blob);
    })();
  }

  async function doCopy(id) {
    if (navigator.clipboard && typeof navigator.clipboard.write === 'function' && typeof ClipboardItem !== 'undefined') {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': pngPromiseFor(id) })]);
        toast('Copied image');
        return;
      } catch (_) { /* try the resolved-blob path (some builds reject a promised blob) */ }
      try {
        const png = await pngPromiseFor(id);
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
        toast('Copied image');
        return;
      } catch (e) { warn('copyImage', e); }
    }
    const ok = await copyText(buildThumbUrl(id, 'maxresdefault'));
    toast(ok ? 'Image copy blocked — max-res URL copied instead' : 'Copy failed');
  }

  /* ================================================================ click-focus (Ostendo card) */

  const SVG_X = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
  let focusEl = null;

  function onFocusKey(e) {
    if (e.key === 'Escape' || e.key === 'Esc') { e.preventDefault(); e.stopImmediatePropagation(); closeFocus(); }
  }
  function closeFocus() {
    if (!focusEl) return;
    focusEl.remove(); focusEl = null;
    document.removeEventListener('keydown', onFocusKey, true);
  }
  function fBtn(svg, label, onClick) {
    const b = el('button', 'sf-tg-fbtn');
    b.type = 'button';
    b.innerHTML = svg + '<span>' + label + '</span>';
    b.addEventListener('click', e => { stop(e); onClick(); });
    return b;
  }
  function openFocus(id) {
    safe('openFocus', () => {
      closeFocus();
      const entry = boxes.get(id) || {};
      const title = entry.title || '';
      focusEl = el('div', 'sf-tg-focus');
      focusEl.addEventListener('click', e => { if (e.target === focusEl) closeFocus(); });

      const card = el('div', 'sf-tg-fcard');
      const bar = el('div', 'sf-tg-ftoolbar');
      const sep = () => { const x = el('span', 'sf-tg-fsep', '//'); x.setAttribute('aria-hidden', 'true'); return x; };
      bar.appendChild(fBtn(SVG_COPY, 'Copy image', () => doCopy(id)));
      bar.appendChild(sep());

      // DOWNLOAD reveals an inline split option selector directly under the bar (right-aligned):
      //   [ DOWNLOAD ] -> RAW THUMBNAIL / TILE (WITH TITLE)
      const opts = el('div', 'sf-tg-fopts');
      const dbtn = fBtn(SVG_DL, 'Download', () => {
        const open = opts.classList.toggle('sf-tg-open');
        dbtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      dbtn.setAttribute('aria-expanded', 'false');
      bar.appendChild(dbtn);
      bar.appendChild(sep());
      bar.appendChild(fBtn(SVG_X, 'Close', closeFocus));
      card.appendChild(bar);

      opts.appendChild(el('span', 'sf-tg-flabel', '[ DOWNLOAD ]'));
      const arrow = el('span', 'sf-tg-farrow', '➔'); arrow.setAttribute('aria-hidden', 'true');
      opts.appendChild(arrow);
      const mThumb = el('button', 'sf-tg-fopt', 'Raw thumbnail'); mThumb.type = 'button';
      mThumb.addEventListener('click', e => { stop(e); opts.classList.remove('sf-tg-open'); dbtn.setAttribute('aria-expanded', 'false'); doDownload(id, false); });
      const slash = el('span', 'sf-tg-fsep', '/'); slash.setAttribute('aria-hidden', 'true');
      const mTile = el('button', 'sf-tg-fopt', 'Tile (with title)'); mTile.type = 'button';
      mTile.addEventListener('click', e => { stop(e); opts.classList.remove('sf-tg-open'); dbtn.setAttribute('aria-expanded', 'false'); doDownloadTile(id); });
      opts.appendChild(mThumb); opts.appendChild(slash); opts.appendChild(mTile);
      card.appendChild(opts);

      const im = el('img', 'sf-tg-fimg');
      im.referrerPolicy = 'no-referrer'; im.alt = '';
      im.src = buildThumbUrl(id, 'maxresdefault');
      im.addEventListener('error', () => { const hq = buildThumbUrl(id, 'hqdefault'); if (im.src !== hq) im.src = hq; }, { once: true });
      card.appendChild(im);
      if (title) card.appendChild(el('div', 'sf-tg-fcap', title));

      focusEl.appendChild(card);
      document.body.appendChild(focusEl);
      document.addEventListener('keydown', onFocusKey, true);
    });
  }

  // A "tile" = the video exactly as it sits in the YouTube feed: rounded thumbnail, channel
  // avatar, title (2 lines), channel name, views • age — on the page's own theme.
  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('decode failed'));
      img.src = src;
    });
  }
  // Avatars are cross-origin (yt3.ggpht.com): draw them from a same-origin blob so the canvas
  // stays exportable. Direct fetch first, background fallback (host permission), else null.
  async function getAvatarBlob(url) {
    if (!url) return null;
    try {
      const res = await fetch(url, { cache: 'force-cache', mode: 'cors' });
      if (res.ok) { const b = await res.blob(); if (b.size > 200) return b; }
    } catch (_) { /* CORS -> background */ }
    try {
      const r = await send({ type: 'imageBlob', url });
      if (r && r.ok && r.dataUrl) return await (await fetch(r.dataUrl)).blob();
    } catch (e) { warn('avatar', e); }
    return null;
  }
  function roundRect(g, x, y, w, h, r) {
    g.beginPath();
    g.moveTo(x + r, y); g.lineTo(x + w - r, y); g.quadraticCurveTo(x + w, y, x + w, y + r);
    g.lineTo(x + w, y + h - r); g.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    g.lineTo(x + r, y + h); g.quadraticCurveTo(x, y + h, x, y + h - r);
    g.lineTo(x, y + r); g.quadraticCurveTo(x, y, x + r, y); g.closePath();
  }
  function wrapLines(g, text, maxW, maxLines) {
    const words = String(text || '').split(/\s+/).filter(Boolean);
    const lines = []; let line = '';
    for (const w of words) {
      const test = line ? line + ' ' + w : w;
      if (g.measureText(test).width > maxW && line) { lines.push(line); line = w; } else line = test;
    }
    if (line) lines.push(line);
    if (lines.length > maxLines) {
      const keep = lines.slice(0, maxLines);
      let last = keep[maxLines - 1];
      while (last && g.measureText(last + '…').width > maxW) last = last.replace(/\s*\S+$/, '');
      keep[maxLines - 1] = (last || '') + '…';
      return keep;
    }
    return lines;
  }
  function pageIsDark() {
    try { return document.documentElement.hasAttribute('dark') || document.documentElement.getAttribute('dark') === 'true'; }
    catch (_) { return true; }
  }
  async function composeTile(thumbBlob, title, meta) {
    meta = meta || {};
    const dark = pageIsDark();
    const bg = dark ? '#0f0f0f' : '#ffffff', fg = dark ? '#f1f1f1' : '#0f0f0f', dim = dark ? '#aaaaaa' : '#606060';
    const W = 1280, PAD = 0, TH = 720, R = 24;
    const thumbUrl = URL.createObjectURL(thumbBlob);
    let avatarUrl = null, avatarImg = null;
    try {
      const img = await loadImage(thumbUrl);
      const ab = await getAvatarBlob(meta.avatar);
      if (ab) { avatarUrl = URL.createObjectURL(ab); try { avatarImg = await loadImage(avatarUrl); } catch (_) { avatarImg = null; } }
      const probe = document.createElement('canvas').getContext('2d');
      probe.font = '500 36px Roboto, "YouTube Sans", Arial, sans-serif';
      const AV = 80, GAP = 24, TX = PAD + AV + GAP, TW = W - TX - 24;
      const titleLines = wrapLines(probe, title || '', TW, 2);
      const LH = 46, SUB = 30;
      const sub1 = meta.channel || '';
      const sub2 = [meta.views, meta.age].filter(Boolean).join(' • ');
      const textH = titleLines.length * LH + (sub1 ? SUB + 6 : 0) + (sub2 ? SUB + 2 : 0);
      const H = TH + 28 + Math.max(AV, textH) + 36;
      const c = document.createElement('canvas');
      c.width = W; c.height = H;
      const g = c.getContext('2d');
      g.fillStyle = bg; g.fillRect(0, 0, W, H);
      // thumbnail, cover-fit, rounded like the feed
      g.save(); roundRect(g, PAD, 0, W - PAD * 2, TH, R); g.clip();
      const s = Math.max((W - PAD * 2) / img.naturalWidth, TH / img.naturalHeight);
      const iw = img.naturalWidth * s, ih = img.naturalHeight * s;
      g.drawImage(img, PAD + ((W - PAD * 2) - iw) / 2, (TH - ih) / 2, iw, ih);
      g.restore();
      // avatar
      const ay = TH + 28;
      g.save(); g.beginPath(); g.arc(PAD + AV / 2, ay + AV / 2, AV / 2, 0, Math.PI * 2); g.closePath(); g.clip();
      if (avatarImg) g.drawImage(avatarImg, PAD, ay, AV, AV);
      else {
        g.fillStyle = dark ? '#3ea6ff' : '#065fd4'; g.fillRect(PAD, ay, AV, AV);
        g.fillStyle = '#fff'; g.font = '500 40px Roboto, Arial, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
        g.fillText((sub1 || '?').trim().charAt(0).toUpperCase(), PAD + AV / 2, ay + AV / 2 + 2);
        g.textAlign = 'left';
      }
      g.restore();
      // title + channel + meta
      g.textBaseline = 'alphabetic'; g.textAlign = 'left';
      g.fillStyle = fg; g.font = '500 36px Roboto, "YouTube Sans", Arial, sans-serif';
      let y = ay + 38;
      for (const ln of titleLines) { g.fillText(ln, TX, y); y += LH; }
      g.fillStyle = dim; g.font = '400 28px Roboto, "YouTube Sans", Arial, sans-serif';
      if (sub1) { y += 4; g.fillText(sub1, TX, y); y += SUB + 2; }
      if (sub2) { g.fillText(sub2, TX, y); }
      return await new Promise((resolve, reject) => c.toBlob(b => (b ? resolve(b) : reject(new Error('toBlob null'))), 'image/png'));
    } finally {
      URL.revokeObjectURL(thumbUrl);
      if (avatarUrl) URL.revokeObjectURL(avatarUrl);
    }
  }
  async function doDownloadTile(id) {
    const t = await getThumb(id);
    if (!t) { toast('Thumbnail unavailable'); return; }
    const entry = boxes.get(id) || {};
    const title = entry.title || '';
    try {
      const png = await composeTile(t.blob, title, entry.meta);
      const url = URL.createObjectURL(png);
      const a = el('a');
      a.href = url; a.download = sanitizeFilename(title || id) + ' - tile.png';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => { try { URL.revokeObjectURL(url); } catch (_) { /* ignore */ } }, 8000);
      toast('Saved tile — ' + a.download);
    } catch (e) { warn('tile', e); toast('Tile failed'); }
  }

  /* ================================================================ boxes + layers */

  function stop(e) { e.preventDefault(); e.stopPropagation(); }

  function mkBtn(svg, tip, onClick) {
    const b = el('button', 'sf-tg-btn');
    b.type = 'button';
    b.title = tip;
    b.setAttribute('aria-label', tip);
    b.innerHTML = svg;
    b.addEventListener('click', e => { stop(e); onClick(); });
    return b;
  }

  function buildBox(id, info) {
    const box = el('div', 'sf-tg-box');
    box.dataset.sfId = id;

    const img = el('img', 'sf-tg-img');
    img.alt = '';
    img.decoding = 'async';
    img.referrerPolicy = 'no-referrer';
    img.src = info.src;
    img.addEventListener('error', () => { const hq = buildThumbUrl(id, 'hqdefault'); if (img.src !== hq) img.src = hq; }, { once: true });
    box.appendChild(img);

    const bar = el('div', 'sf-tg-bar');
    bar.appendChild(mkBtn(SVG_DL, 'Download thumbnail — saves ' + id + '.jpg', () => doDownload(id, false)));
    bar.appendChild(mkBtn(SVG_TAG, 'Download + title — saves the video title as the file name', () => doDownload(id, true)));
    bar.appendChild(mkBtn(SVG_COPY, 'Copy image to clipboard', () => doCopy(id)));
    box.appendChild(bar);

    const cap = el('div', 'sf-tg-title', info.title || '');
    if (info.title) cap.title = info.title;
    box.appendChild(cap);

    // Click the thumbnail body (not a bar button) -> the focused Ostendo card.
    box.addEventListener('click', e => { if (e.target.closest('.sf-tg-bar')) return; stop(e); openFocus(id); });

    return box;
  }

  function updateEntryTitle(entry, title) {
    if (!title || entry.title === title) return;
    entry.title = title;
    const cap = entry.box.querySelector('.sf-tg-title');
    if (cap) { cap.textContent = title; cap.title = title; }
  }

  function ensureLayers() {
    if (!document.body) return;
    if (!overlay || !overlay.isConnected) {
      overlay = el('div', 'sf-tg-overlay');
      overlay.addEventListener('click', () => safe('backdrop', deactivate));
      document.body.appendChild(overlay);
    }
    if (!layer || !layer.isConnected) {
      // The old boxes (if any) went away with the old layer.
      for (const [, e] of boxes) { try { e.box.remove(); } catch (_) { /* ignore */ } }
      boxes.clear();
      layer = el('div', 'sf-tg-layer');
      document.body.appendChild(layer);
    }
  }

  function rescan() {
    if (!active) return;
    ensureLayers();
    if (!layer) return;
    const found = collectThumbs();
    for (const [id, entry] of boxes) {
      if (!found.has(id)) { entry.box.remove(); boxes.delete(id); }
    }
    for (const [id, info] of found) {
      let entry = boxes.get(id);
      if (!entry) {
        const box = buildBox(id, info);
        layer.appendChild(box);
        entry = { box, target: info.target, title: info.title, meta: info.meta };
        boxes.set(id, entry);
      } else {
        entry.target = info.target;
        if (info.meta && (info.meta.channel || info.meta.avatar)) entry.meta = info.meta;
        updateEntryTitle(entry, info.title);
      }
    }
    reposition();
  }
  const scheduleRescan = SF.debounce(() => safe('rescan', rescan), 180);

  function reposition() {
    if (!active || !layer) return;
    const vh = window.innerHeight, vw = window.innerWidth;
    for (const [id, entry] of boxes) {
      const t = entry.target;
      if (!t || !t.isConnected) { entry.box.remove(); boxes.delete(id); continue; }
      const r = t.getBoundingClientRect();
      if (r.width < 40 || r.height < 24 || r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) {
        entry.box.style.display = 'none';
        continue;
      }
      const b = entry.box;
      b.style.display = '';
      b.style.width = Math.round(r.width) + 'px';
      b.style.height = Math.round(r.height) + 'px';
      b.style.transform = 'translate(' + Math.round(r.left) + 'px,' + Math.round(r.top) + 'px)';
    }
  }
  function scheduleReposition() {
    if (repositionRaf) return;
    repositionRaf = requestAnimationFrame(() => { repositionRaf = 0; safe('reposition', reposition); });
  }

  /* ================================================================ activate / deactivate */

  // Scroll: reposition existing boxes every frame (smooth), and schedule a rescan so thumbnails
  // that scroll in beyond the pre-rendered margin pick up a box shortly after a pause.
  function onScroll() { scheduleReposition(); scheduleRescan(); }
  function onResize() { scheduleReposition(); scheduleRescan(); }
  function onKey(e) {
    if (!active) return;
    if (e.key === 'Escape' || e.key === 'Esc') { e.preventDefault(); e.stopImmediatePropagation(); deactivate(); }
  }

  function activate() {
    if (active || !document.body) return;
    active = true;
    document.documentElement.classList.add('sf-tg-active');
    ensureLayers();
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize, true);
    document.addEventListener('keydown', onKey, true);
    activeObserver = new MutationObserver(scheduleRescan);
    try { activeObserver.observe(document.documentElement, { childList: true, subtree: true }); } catch (e) { warn('observe', e); }
    rescan();
    updateFab();
    toast('Grab thumbnails: hover one to download or copy · Esc to exit');
  }

  function deactivate() {
    if (!active) return;
    active = false;
    window.removeEventListener('scroll', onScroll, true);
    window.removeEventListener('resize', onResize, true);
    document.removeEventListener('keydown', onKey, true);
    if (activeObserver) { try { activeObserver.disconnect(); } catch (_) { /* ignore */ } activeObserver = null; }
    if (repositionRaf) { cancelAnimationFrame(repositionRaf); repositionRaf = 0; }
    for (const [, e] of boxes) { try { e.box.remove(); } catch (_) { /* ignore */ } }
    boxes.clear();
    if (layer) { layer.remove(); layer = null; }
    if (overlay) { overlay.remove(); overlay = null; }
    document.documentElement.classList.remove('sf-tg-active');
    updateFab();
  }

  function toggle() { if (active) deactivate(); else activate(); }

  /* ================================================================ floating button */

  function updateFab() {
    const fab = document.querySelector('.sf-tg-fab');
    if (!fab) return;
    fab.classList.toggle('sf-tg-on', active);
    fab.setAttribute('aria-pressed', active ? 'true' : 'false');
    fab.title = active ? 'CreatorHaven: exit thumbnail grab' : 'CreatorHaven: grab thumbnails';
  }

  function removeFab() {
    for (const f of document.querySelectorAll('.sf-tg-fab')) f.remove();
  }

  function ensureFab() {
    if (!settings.thumbGrabber) { removeFab(); if (active) deactivate(); return; }
    if (!document.body) return;
    let fab = document.querySelector('.sf-tg-fab');
    if (fab && fab.isConnected) { updateFab(); return; }
    if (fab) fab.remove();
    fab = el('button', 'sf-tg-fab');
    fab.type = 'button';
    fab.setAttribute('aria-label', 'CreatorHaven: grab thumbnails');
    fab.setAttribute('aria-pressed', 'false');
    fab.innerHTML = SVG_CAMERA + '<span class="sf-tg-fabtxt" aria-hidden="true">GRAB</span>';
    fab.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); safe('toggle', toggle); });
    document.body.appendChild(fab);
    updateFab();
  }
  const scheduleEnsure = SF.debounce(() => safe('ensureFab', ensureFab), 300);

  /* ================================================================ engine */

  async function loadSettings() {
    try {
      const raw = await api.storage.sync.get(SF.SF_DEFAULTS);
      settings = SF.withDefaults(raw);
    } catch (e) { warn('settings', e); }
  }

  function installSettingsSync() {
    api.storage.onChanged.addListener((changes, area) => {
      if (area !== 'sync') return;
      safe('onChanged', () => {
        if (!('thumbGrabber' in changes)) return;
        const v = changes.thumbGrabber.newValue;
        settings.thumbGrabber = (v === undefined) ? SF.SF_DEFAULTS.thumbGrabber : !(v === false || v === 'false' || v === '0');
        ensureFab();
      });
    });
  }

  function onNavigate() {
    scheduleEnsure();
    if (active) scheduleRescan();
  }

  function installNavHooks() {
    for (const ev of ['yt-navigate-finish', 'yt-page-data-updated', 'yt-navigate-start']) {
      window.addEventListener(ev, onNavigate, true);
      document.addEventListener(ev, onNavigate, true);
    }
    // Belt and braces: re-assert the FAB if a hard navigation wiped it (cheap querySelector).
    setInterval(() => safe('ensureFab', ensureFab), 2000);
  }

  function whenReady(cb) {
    if (document.body) { cb(); return; }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', cb, { once: true });
    else cb();
  }

  async function init() {
    installSettingsSync();
    installNavHooks();
    await loadSettings();
    whenReady(() => safe('ensureFab', ensureFab));
  }

  init().catch(e => warn('init', e));
})();
