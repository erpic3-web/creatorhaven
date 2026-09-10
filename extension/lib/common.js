/* CreatorHaven — lib/common.js
 *
 * Pure helpers shared by bg.js (service worker / event page), both content scripts,
 * the popup, the tester page and the Node unit tests (test/run.js).
 *
 * Rules for this file:
 *   - NO DOM access (Node must be able to require() it).
 *   - Plain ES2020, classic script: no import/export.
 *   - Attaches itself to globalThis.SF for browser scripts and to module.exports for Node.
 */
(function (root) {
  'use strict';

  /* ------------------------------------------------------------------ settings */

  // Every setting the extension knows about, with its default. bg.js seeds these into
  // storage.sync on install; everything else reads through withDefaults().
  // NOTE: "favorites" is the feature TOGGLE. The starred-channel list is stored under
  // "favoritesList" so the two keys cannot collide.
  const SF_DEFAULTS = Object.freeze({
    siteUrl: 'http://127.0.0.1:5800',
    token: '',
    discordUrl: '',
    confirmSignOut: true,
    exactDates: true,
    realNames: true,
    subCounts: true,
    feedCleaner: true,
    shortsRedirect: false,
    dontRecommend: false,
    thumbDownloader: true,
    thumbGrabber: true,
    playlistSearch: true,
    videoTags: true,
    appearance: false,
    appearanceAccent: '#ff0000',
    appearanceFont: '',
    appearanceCompact: false,
    sizeCustomizer: false,
    playerWidthPct: 100,
    commentsFontPx: 14,
    favorites: true,
    studioProbe: true,
    studioDiscordButton: true,
    streamerMode: false,
    // Studio-side power features (studio.youtube.com)
    studioPanel: true,          // the floating CreatorHaven research + analytics-decoder panel
    studioOutlierAll: true,     // outlier score vs ALL uploads, not just the last 10
    studioExplainers: true,     // ⓘ hover cards explaining each analytics metric
    studioAdPlacer: false,      // show the "place mid-rolls every N min" control (experimental)
    studioAdInterval: 5,        // default minutes between auto-placed mid-rolls
    studioRemoveRedAds: false,  // show the "remove unapproved (red) ad breaks" control (experimental)
    // Watch-page / feed power features (www.youtube.com)
    youtubeOutliers: true       // show an outlier multiplier (2.3×) next to video titles while browsing
  });

  const BOOL_KEYS = Object.freeze(Object.keys(SF_DEFAULTS).filter(k => typeof SF_DEFAULTS[k] === 'boolean'));

  // Shared constants (single source of truth for bg + content scripts + tests).
  const HANDLE_CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000;   // positive lookups: 7 days
  const HANDLE_NEG_TTL_MS = 24 * 60 * 60 * 1000;         // "site does not know this handle": 1 day
  const MAX_HANDLES_PER_REQUEST = 50;
  const PROBE_LOG_CAP = 300;
  const REALTIME_MIN_INTERVAL_MS = 5000;
  const THUMB_MIN_BYTES = 3000;                          // i.ytimg.com 120x90 placeholder is ~1 KB

  // Merge a raw storage object over the defaults, coercing each value to the default's type.
  function withDefaults(raw) {
    const out = Object.assign({}, SF_DEFAULTS);
    if (raw && typeof raw === 'object') {
      for (const k of Object.keys(SF_DEFAULTS)) {
        const v = raw[k];
        if (v === undefined || v === null) continue;
        const def = SF_DEFAULTS[k];
        if (typeof def === 'boolean') out[k] = (v === 'false' || v === '0') ? false : !!v;
        else if (typeof def === 'number') { const n = Number(v); out[k] = Number.isFinite(n) ? n : def; }
        else out[k] = String(v);
      }
    }
    return out;
  }

  // "127.0.0.1:5800/" -> "http://127.0.0.1:5800"; empty -> default.
  function normalizeSiteUrl(u) {
    let s = String(u == null ? '' : u).trim();
    if (!s) return SF_DEFAULTS.siteUrl;
    if (!/^https?:\/\//i.test(s)) s = 'http://' + s;
    return s.replace(/\/+$/, '');
  }

  /* ------------------------------------------------------------------ YouTube URLs */

  const VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;
  const CHANNEL_ID_RE = /^UC[A-Za-z0-9_-]{22}$/;
  const HANDLE_RE = /^@[A-Za-z0-9._-]{3,30}$/;

  function ytHost(hostname) {
    return String(hostname || '').toLowerCase().replace(/^(www|m|music|gaming)\./, '');
  }

  // Video id from any YouTube video URL shape; null when there is none.
  //   /watch?v=ID (any extra params, any order), youtu.be/ID, /shorts/ID, /embed/ID,
  //   /live/ID, /v/ID, /e/ID. Relative URLs resolve against www.youtube.com.
  function parseVideoId(input) {
    if (!input) return null;
    let u;
    try { u = new URL(String(input).trim(), 'https://www.youtube.com/'); } catch (_) { return null; }
    const host = ytHost(u.hostname);
    const path = u.pathname;
    let id = null;
    if (host === 'youtu.be') {
      id = path.split('/')[1] || null;
    } else if (/(^|\.)youtube(-nocookie)?\.com$/.test(host)) {
      if (path === '/watch' || path === '/watch/') id = u.searchParams.get('v');
      else {
        const m = path.match(/^\/(?:shorts|embed|live|v|e)\/([^/?#]+)/);
        if (m) id = m[1];
        else if (path.startsWith('/watch/')) id = path.slice('/watch/'.length).split('/')[0];
      }
    }
    if (id) { try { id = decodeURIComponent(id); } catch (_) { /* keep raw */ } }
    return id && VIDEO_ID_RE.test(id) ? id : null;
  }

  // Channel reference from a URL or a bare handle / id.
  //   -> { type:'handle', handle:'@name' } | { type:'id', id:'UC…' }
  //    | { type:'custom', name } (/c/name) | { type:'user', name } (/user/name) | null
  function parseChannelRef(input) {
    if (!input) return null;
    const s = String(input).trim();
    if (HANDLE_RE.test(s)) return { type: 'handle', handle: s };
    if (CHANNEL_ID_RE.test(s)) return { type: 'id', id: s };
    let u;
    try { u = new URL(s, 'https://www.youtube.com/'); } catch (_) { return null; }
    if (!/(^|\.)youtube\.com$/.test(ytHost(u.hostname))) return null;
    const parts = u.pathname.split('/').filter(Boolean).map(p => { try { return decodeURIComponent(p); } catch (_) { return p; } });
    if (!parts.length) return null;
    const first = parts[0];
    if (first.startsWith('@')) return HANDLE_RE.test(first) ? { type: 'handle', handle: first } : null;
    if (first === 'channel') return parts[1] && CHANNEL_ID_RE.test(parts[1]) ? { type: 'id', id: parts[1] } : null;
    if (first === 'c') return parts[1] ? { type: 'custom', name: parts[1] } : null;
    if (first === 'user') return parts[1] ? { type: 'user', name: parts[1] } : null;
    return null;
  }

  // "@Name" / "name" -> "@Name"; junk -> "".
  function normalizeHandle(h) {
    let s = String(h == null ? '' : h).trim();
    if (!s) return '';
    if (!s.startsWith('@')) s = '@' + s;
    s = s.replace(/\.+$/, '');
    return HANDLE_RE.test(s) ? s : '';
  }

  // storage.local key for the per-handle cache (case-insensitive).
  function handleKey(h) {
    const n = normalizeHandle(h);
    return n ? 'hn:' + n.toLowerCase() : '';
  }

  // Unique @handles found in a string or an array of strings, first-seen order.
  const HANDLE_IN_TEXT_RE = /(^|[^A-Za-z0-9_@.-])(@[A-Za-z0-9._-]{3,30})/g;
  function extractHandles(texts) {
    const list = Array.isArray(texts) ? texts : [texts];
    const seen = new Set();
    const out = [];
    for (const t of list) {
      if (typeof t !== 'string' || !t.includes('@')) continue;
      HANDLE_IN_TEXT_RE.lastIndex = 0;
      let m;
      while ((m = HANDLE_IN_TEXT_RE.exec(t)) !== null) {
        const h = normalizeHandle(m[2]);
        if (!h) continue;
        const k = h.toLowerCase();
        if (seen.has(k)) continue;
        seen.add(k);
        out.push(h);
      }
    }
    return out;
  }

  // /channel/UC…/… -> "UC…" (used by the Studio probe to tag realtime samples).
  function channelIdFromPath(pathname) {
    const m = String(pathname || '').match(/\/channel\/(UC[A-Za-z0-9_-]{22})(?=\/|$)/);
    return m ? m[1] : null;
  }

  // Path only, no query/hash. Relative input resolves against studio.youtube.com.
  function pathOnly(url) {
    try { return new URL(String(url), 'https://studio.youtube.com/').pathname; }
    catch (_) { return String(url == null ? '' : url).split(/[?#]/)[0]; }
  }

  /* ------------------------------------------------------------------ thumbnails */

  // Ordered best -> worst. "maxresdefault" is missing (404 or a 120x90 placeholder) for
  // many older uploads, hence the fallback walk in bg.js.
  const THUMB_SIZES = Object.freeze(['maxresdefault', 'sddefault', 'hqdefault', 'mqdefault', 'default']);

  // pickThumbUrl('abc', 'maxres' | 'sd' | 'hq' | 'mq' | 'default' | full name)
  function pickThumbUrl(id, size) {
    const s = String(size || 'maxresdefault');
    const name = THUMB_SIZES.includes(s) ? s : (THUMB_SIZES.find(x => x.startsWith(s)) || 'maxresdefault');
    return 'https://i.ytimg.com/vi/' + encodeURIComponent(String(id)) + '/' + name + '.jpg';
  }

  function isPlaceholderThumb(bytes) {
    const n = Number(bytes);
    return !Number.isFinite(n) || n < THUMB_MIN_BYTES;
  }

  /* ------------------------------------------------------------------ dates */

  // "3 weeks ago", "Streamed 2 days ago", "Premiered 1 hour ago", "1 year ago".
  // Deliberately NOT: "3 weeks", "ago", "Sep 8, 2026".
  const relativeDateRegex = /\b(?:(?:Streamed|Premiered|Updated)\s+)?\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b/i;

  // ISO (or Date) -> "Sep 8, 2026, 3:41 PM" in the viewer's local time zone. '' if invalid.
  function formatExactDate(iso, extra) {
    const d = iso instanceof Date ? iso : new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const opts = Object.assign({ month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }, extra || {});
    try { return new Intl.DateTimeFormat('en-US', opts).format(d); }
    catch (_) { return d.toLocaleString(); }
  }

  // "publishDate":"2026-09-08T15:41:32-07:00" inside inline script text.
  function extractPublishDate(text) {
    const m = String(text || '').match(/"publishDate"\s*:\s*"([^"]{4,64})"/);
    return m ? m[1] : null;
  }

  /* ------------------------------------------------------------------ numbers */

  // 1234 -> "1.2K", 1250000 -> "1.3M", 999 -> "999", 999950 -> "1M". One decimal, ".0" stripped.
  function compactNumber(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return '';
    const sign = v < 0 ? '-' : '';
    const abs = Math.abs(v);
    const units = [['B', 1e9], ['M', 1e6], ['K', 1e3]];
    for (let i = 0; i < units.length; i++) {
      const suf = units[i][0], div = units[i][1];
      if (abs < div) continue;
      let val = Math.round(abs / div * 10) / 10;
      let s = suf;
      if (val >= 1000 && i > 0) { val = 1; s = units[i - 1][0]; } // 999,950 -> "1M", not "1000K"
      return sign + String(val) + s;
    }
    return sign + String(Math.round(abs));
  }

  // Streamer-mode blur: any numeric token with 2+ digits, optionally with , . and a K/M/B suffix.
  //   "1,234" ✓  "12.5K" ✓  "12 hours" ✓ (the "12")  "2 hours" ✗  "5K" ✗ (single digit)
  const NUMBER_BLUR_SOURCE = '\\b\\d[\\d,.]*\\d(?:\\s?[KMB](?![A-Za-z]))?';
  const numberBlurRegex = new RegExp(NUMBER_BLUR_SOURCE, 'i');   // non-global: safe for .test()

  function hasNumberRun(text) {
    return typeof text === 'string' && /\d/.test(text) && numberBlurRegex.test(text);
  }

  // Split text into [{ text, blur }] segments. Pure inverse of joinSegments().
  // The DOM wrap/unwrap lives in content/studio.js (this file has no DOM).
  function segmentNumbers(text) {
    const s = String(text == null ? '' : text);
    const out = [];
    if (!s) return out;
    const re = new RegExp(NUMBER_BLUR_SOURCE, 'gi');
    let last = 0, m;
    while ((m = re.exec(s)) !== null) {
      if (m[0].length === 0) { re.lastIndex++; continue; }
      if (m.index > last) out.push({ text: s.slice(last, m.index), blur: false });
      out.push({ text: m[0], blur: true });
      last = m.index + m[0].length;
    }
    if (last < s.length) out.push({ text: s.slice(last), blur: false });
    return out;
  }

  function joinSegments(segs) { return (segs || []).map(x => x.text).join(''); }

  /* ------------------------------------------------------------------ generic */

  function clamp(n, lo, hi) {
    const v = Number(n);
    if (!Number.isFinite(v)) return lo;
    return Math.min(hi, Math.max(lo, v));
  }

  // Trailing-edge debounce with .cancel() and .flush().
  function debounce(fn, ms) {
    let timer = null, lastArgs = null, lastThis = null;
    const wrapped = function () {
      lastArgs = arguments; lastThis = this;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { timer = null; const a = lastArgs; lastArgs = null; fn.apply(lastThis, a); }, ms);
    };
    wrapped.cancel = () => { if (timer) clearTimeout(timer); timer = null; lastArgs = null; };
    wrapped.flush = () => { if (timer) { clearTimeout(timer); timer = null; const a = lastArgs; lastArgs = null; fn.apply(lastThis, a); } };
    return wrapped;
  }

  function chunk(arr, n) {
    const size = Math.max(1, n | 0);
    const out = [];
    for (let i = 0; i < (arr || []).length; i += size) out.push(arr.slice(i, i + size));
    return out;
  }

  function uniq(arr) { return Array.from(new Set(arr || [])); }

  function safeJsonParse(text) {
    try { return JSON.parse(text); } catch (_) { return undefined; }
  }

  // Feed cleaner: shelf headers that get hidden (case-insensitive).
  const shelfBlockRegex = /\b(?:shorts|playables|latest youtube posts|breaking news)\b/i;

  /* ------------------------------------------------------------------ studio probe */

  // Key names worth surfacing from Studio's internal (youtubei) JSON responses.
  const probeHitKeyRegex = /realtime|last48|last60|fortyEight|sixtyMin|engaged|monetiz|adBreak|midroll/i;

  // Walk a parsed JSON value (depth <= 8, <= 5000 nodes, <= 200 hits) and collect keys
  // matching probeHitKeyRegex with their path and (number / short-string) value.
  // NOTE: content/studio_probe_main.js carries its own copy of this walker because it runs
  // in the page's MAIN world where lib/common.js is deliberately not loaded; test/run.js
  // asserts both regexes stay identical.
  function collectProbeHits(value, opts) {
    const maxDepth = (opts && opts.maxDepth) || 8;
    const maxNodes = (opts && opts.maxNodes) || 5000;
    const maxHits = (opts && opts.maxHits) || 200;
    const hits = [];
    let nodes = 0;
    const stack = [{ v: value, path: '$', depth: 0 }];
    while (stack.length) {
      const cur = stack.pop();
      const v = cur.v;
      if (v === null || typeof v !== 'object') continue;
      if (++nodes > maxNodes) break;
      if (cur.depth >= maxDepth) continue;
      const isArr = Array.isArray(v);
      const keys = isArr ? null : Object.keys(v);
      const len = isArr ? v.length : keys.length;
      for (let i = 0; i < len; i++) {
        const k = isArr ? i : keys[i];
        const child = v[k];
        const childPath = isArr ? cur.path + '[' + k + ']' : cur.path + '.' + k;
        if (!isArr && probeHitKeyRegex.test(k)) {
          let val;
          if (typeof child === 'number') val = child;
          else if (typeof child === 'string' && child.length <= 40) val = child;
          const hit = { path: childPath, key: k, type: Array.isArray(child) ? 'array' : (child === null ? 'null' : typeof child) };
          if (val !== undefined) hit.value = val;
          hits.push(hit);
          if (hits.length >= maxHits) return hits;
        }
        if (child && typeof child === 'object') stack.push({ v: child, path: childPath, depth: cur.depth + 1 });
      }
    }
    return hits;
  }

  // Rolling probe log: dedupe by url+status (count occurrences, keep first-seen keys,
  // union hits by path up to 50), newest-first-seen entries drop off past `cap`.
  function mergeProbeEntry(log, entry, cap) {
    const limit = cap || PROBE_LOG_CAP;
    const list = Array.isArray(log) ? log.slice() : [];
    if (!entry || typeof entry !== 'object') return list.slice(-limit);
    const url = String(entry.url || '');
    const status = Number(entry.status) || 0;
    const ts = Number(entry.ts) || Date.now();
    const hits = Array.isArray(entry.hits) ? entry.hits : [];
    const idx = list.findIndex(e => e && e.url === url && e.status === status);
    if (idx >= 0) {
      const prev = list[idx];
      const merged = Object.assign({}, prev, {
        count: (prev.count || 1) + 1,
        lastSeen: ts,
        lastSize: Number(entry.size) || 0
      });
      if (hits.length) {
        const seen = new Set((prev.hits || []).map(h => h.path));
        const combined = (prev.hits || []).slice();
        for (const h of hits) { if (h && !seen.has(h.path) && combined.length < 50) { seen.add(h.path); combined.push(h); } }
        merged.hits = combined;
      }
      list[idx] = merged;
      return list;
    }
    list.push({
      url, status,
      keys: Array.isArray(entry.keys) ? entry.keys.slice(0, 60) : [],
      size: Number(entry.size) || 0,
      lastSize: Number(entry.size) || 0,
      firstSeen: ts, lastSeen: ts, count: 1,
      hits: hits.slice(0, 50)
    });
    while (list.length > limit) list.shift();
    return list;
  }

  /* ------------------------------------------------------------------ export */

  const SF = {
    SF_DEFAULTS, BOOL_KEYS, withDefaults, normalizeSiteUrl,
    HANDLE_CACHE_TTL_MS, HANDLE_NEG_TTL_MS, MAX_HANDLES_PER_REQUEST, PROBE_LOG_CAP,
    REALTIME_MIN_INTERVAL_MS, THUMB_MIN_BYTES, THUMB_SIZES,
    parseVideoId, parseChannelRef, normalizeHandle, handleKey, extractHandles,
    channelIdFromPath, pathOnly, pickThumbUrl, isPlaceholderThumb,
    relativeDateRegex, formatExactDate, extractPublishDate,
    compactNumber, NUMBER_BLUR_SOURCE, numberBlurRegex, hasNumberRun, segmentNumbers, joinSegments,
    clamp, debounce, chunk, uniq, safeJsonParse, shelfBlockRegex,
    probeHitKeyRegex, collectProbeHits, mergeProbeEntry
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = SF;
  if (root) root.SF = SF;
})(typeof globalThis !== 'undefined' ? globalThis : this);
