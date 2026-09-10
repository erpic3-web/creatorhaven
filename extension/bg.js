/* CreatorHaven — background.
 *
 * Chrome : MV3 service worker  (manifest background.service_worker = bg.js, common.js via importScripts)
 * Firefox: MV3 event page      (manifest background.scripts = [lib/common.js, bg.js])
 *
 * Owns: settings defaults, the toolbar badge (site connection state), every network call
 * (site API + i.ytimg.com thumbnails — content scripts never fetch cross-origin themselves),
 * the rolling Studio probe log, and the alarms (periodic ping, cache pruning).
 *
 * Every handler returns a plain object and never throws to its caller; every fetch has a
 * 10 s timeout and collapses to { ok:false, error }.
 */
'use strict';

if (typeof importScripts === 'function' && !globalThis.SF) {
  try { importScripts('lib/common.js'); } catch (e) { console.warn('[SF bg] importScripts failed', e); }
}

const api = globalThis.browser ?? globalThis.chrome;
const SF = globalThis.SF;
const TAG = '[SF bg]';
const FETCH_TIMEOUT_MS = 10000;
const PING_ALARM = 'sf-ping';
const PRUNE_ALARM = 'sf-prune';
const PROBE_FLUSH_MS = 300;

let lastRealtimeAt = 0;            // rate limiter for POST /api/ext/realtime
let probePending = [];             // entries waiting for the batched storage write
let probeFlushTimer = null;
let probeWrite = Promise.resolve(); // serialises read-modify-write of probeLog

/* ------------------------------------------------------------------ settings */

async function getSettings() {
  try {
    const raw = await api.storage.sync.get(SF.SF_DEFAULTS);
    return SF.withDefaults(raw);
  } catch (e) {
    console.warn(TAG, 'settings read failed', e);
    return SF.withDefaults({});
  }
}

// Seed defaults without overwriting anything the user already changed.
async function seedDefaults() {
  try {
    const cur = await api.storage.sync.get(null);
    const patch = {};
    for (const [k, v] of Object.entries(SF.SF_DEFAULTS)) if (!(k in cur)) patch[k] = v;
    if (!Array.isArray(cur.favoritesList)) patch.favoritesList = [];
    if (Object.keys(patch).length) await api.storage.sync.set(patch);
  } catch (e) {
    console.warn(TAG, 'seed defaults failed', e);
  }
}

/* ------------------------------------------------------------------ fetch */

async function fetchWithTimeout(url, init) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, Object.assign({ signal: ctl.signal, cache: 'no-store', credentials: 'omit' }, init || {}));
    return { res };
  } catch (e) {
    return { error: e && e.name === 'AbortError' ? 'timeout after 10s' : String((e && e.message) || e) };
  } finally {
    clearTimeout(timer);
  }
}

// GET/POST {siteUrl}{path} with the X-SF-Token header. Returns { ok, status, data, error, url }.
async function siteRequest(path, opts) {
  const s = await getSettings();
  const base = SF.normalizeSiteUrl(s.siteUrl);
  let url = base + path;
  if (opts && opts.query) {
    const q = new URLSearchParams(opts.query).toString();
    if (q) url += (url.includes('?') ? '&' : '?') + q;
  }
  const headers = { Accept: 'application/json' };
  if (s.token) headers['X-SF-Token'] = s.token;
  const init = { method: (opts && opts.method) || 'GET', headers };
  if (opts && opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  const { res, error } = await fetchWithTimeout(url, init);
  if (error) return { ok: false, error, url };
  let data = null;
  try {
    const text = await res.text();
    data = text ? JSON.parse(text) : null;
  } catch (_) { /* non-JSON body: data stays null */ }
  if (!res.ok) {
    const msg = (data && (data.error || data.message)) || ('HTTP ' + res.status);
    return { ok: false, status: res.status, error: msg, data, url };
  }
  return { ok: true, status: res.status, data, url };
}

/* ------------------------------------------------------------------ badge */

async function setBadge(connected) {
  try {
    if (!api.action) return;
    await api.action.setBadgeText({ text: connected ? '•' : '' });
    if (connected) await api.action.setBadgeBackgroundColor({ color: '#22a559' });
  } catch (_) { /* cosmetic */ }
}

/* ------------------------------------------------------------------ handlers */

async function ping() {
  const r = await siteRequest('/api/ext/ping');
  const ok = !!(r.ok && r.data && r.data.ok);
  await setBadge(ok);
  if (ok) return { ok: true, app: r.data.app || 'site', version: r.data.version || '?', url: r.url };
  return { ok: false, error: r.error || 'site answered without ok:true', status: r.status, url: r.url };
}

async function channels(msg) {
  const handles = SF.uniq((msg.handles || []).map(SF.normalizeHandle).filter(Boolean)).slice(0, SF.MAX_HANDLES_PER_REQUEST);
  const ids = SF.uniq((msg.ids || []).map(x => String(x || '').trim()).filter(Boolean)).slice(0, SF.MAX_HANDLES_PER_REQUEST);
  if (!handles.length && !ids.length) return { ok: true, channels: {} };
  const s = await getSettings();
  if (!s.token) return { ok: false, error: 'no token configured' };
  const query = {};
  if (handles.length) query.handles = handles.join(',');
  if (ids.length) query.ids = ids.join(',');
  const r = await siteRequest('/api/ext/channels', { query });
  if (!r.ok) return { ok: false, error: r.error, status: r.status };
  const map = (r.data && r.data.channels && typeof r.data.channels === 'object') ? r.data.channels : {};
  return { ok: true, channels: map };
}

async function realtime(msg) {
  const now = Date.now();
  if (now - lastRealtimeAt < SF.REALTIME_MIN_INTERVAL_MS) return { ok: false, skipped: 'rate-limited' };
  lastRealtimeAt = now;
  const s = await getSettings();
  if (!s.token) return { ok: false, skipped: 'no token' };
  const body = {
    channelId: msg.channelId || null,
    samples: Array.isArray(msg.hits) ? msg.hits.slice(0, 100) : [],
    url: String(msg.url || ''),
    capturedAt: new Date(Number(msg.ts) || now).toISOString()
  };
  const r = await siteRequest('/api/ext/realtime', { method: 'POST', body });
  return r.ok ? { ok: true } : { ok: false, error: r.error };
}

// Probe entries arrive in bursts (a Studio page load fires dozens of youtubei calls), so
// they are queued and merged into storage in one read-modify-write per 300 ms.
function probe(msg) {
  const e = msg && msg.entry;
  if (!e || typeof e !== 'object') return { ok: false, error: 'no entry' };
  probePending.push({
    url: String(e.url || '').slice(0, 300),
    status: Number(e.status) || 0,
    keys: Array.isArray(e.keys) ? e.keys.slice(0, 60).map(String) : [],
    size: Number(e.size) || 0,
    ts: Number(e.ts) || Date.now(),
    hits: Array.isArray(e.hits) ? e.hits.slice(0, 100) : [],
    full: !!e.full,
    sample: (e.full && typeof e.sample === 'string') ? e.sample.slice(0, 60000) : undefined,
    reqBody: (e.full && typeof e.reqBody === 'string') ? e.reqBody.slice(0, 60000) : undefined
  });
  if (!probeFlushTimer) probeFlushTimer = setTimeout(flushProbe, PROBE_FLUSH_MS);
  return { ok: true, queued: probePending.length };
}

function flushProbe() {
  probeFlushTimer = null;
  const batch = probePending;
  probePending = [];
  if (!batch.length) return probeWrite;
  probeWrite = probeWrite.then(async () => {
    const cur = await api.storage.local.get('probeLog');
    let log = Array.isArray(cur.probeLog) ? cur.probeLog : [];
    for (const entry of batch) log = SF.mergeProbeEntry(log, entry, SF.PROBE_LOG_CAP);
    await api.storage.local.set({ probeLog: log });
  }).catch(e => console.warn(TAG, 'probe write failed', e));
  // Best-effort: mirror ONLY the full-body captures (analytics/channel-list endpoints) to
  // the site so real request shapes arrive automatically — no manual "export log" step.
  (async () => {
    try {
      const s = await getSettings();
      const full = batch.filter(e => e.full && e.sample);
      if (s.token && s.siteUrl && full.length) {
        await siteRequest('/api/ext/probe', { method: 'POST', body: { entries: full } });
      }
    } catch (_) { /* offline is fine */ }
  })();
  return probeWrite;
}

// The Studio-side puller sends normalized per-channel analytics; relay it to the site.
async function extAnalytics(msg) {
  const s = await getSettings();
  if (!s.token) return { ok: false, error: 'no token' };
  if (!s.siteUrl) return { ok: false, error: 'no site url' };
  return siteRequest('/api/ext/analytics', { method: 'POST', body: (msg && msg.payload) || {} });
}

async function probeGet() {
  await flushProbe();
  const cur = await api.storage.local.get('probeLog');
  return { ok: true, log: Array.isArray(cur.probeLog) ? cur.probeLog : [] };
}

async function probeStats() {
  const { log } = await probeGet();
  let total = 0, hits = 0, lastSeen = 0;
  for (const e of log) {
    total += e.count || 1;
    hits += (e.hits || []).length;
    if (e.lastSeen > lastSeen) lastSeen = e.lastSeen;
  }
  return { ok: true, entries: log.length, total, hits, lastSeen };
}

async function probeClear() {
  probePending = [];
  if (probeFlushTimer) { clearTimeout(probeFlushTimer); probeFlushTimer = null; }
  probeWrite = probeWrite.then(() => api.storage.local.set({ probeLog: [] })).catch(() => {});
  await probeWrite;
  return { ok: true };
}

// One i.ytimg.com URL -> { ok, status, bytes, contentType }. Content-Length is trusted when
// present (no body download); otherwise the body is read to measure it.
async function thumb(msg) {
  const url = String((msg && msg.url) || '');
  if (!/^https:\/\/i\.ytimg\.com\//.test(url)) return { ok: false, error: 'only https://i.ytimg.com/ URLs are proxied' };
  const { res, error } = await fetchWithTimeout(url, { method: 'GET' });
  if (error) return { ok: false, error, url };
  if (!res.ok) {
    try { if (res.body && res.body.cancel) await res.body.cancel(); } catch (_) { /* ignore */ }
    return { ok: false, status: res.status, error: 'HTTP ' + res.status, url };
  }
  let bytes = Number(res.headers.get('content-length'));
  if (!Number.isFinite(bytes) || bytes <= 0) {
    try { bytes = (await res.arrayBuffer()).byteLength; } catch (_) { bytes = 0; }
  } else {
    try { if (res.body && res.body.cancel) await res.body.cancel(); } catch (_) { /* ignore */ }
  }
  return { ok: true, status: res.status, bytes, contentType: res.headers.get('content-type') || '', url };
}

// Walk maxres -> sd -> hq -> mq -> default and return the first real image
// (404 and the ~1 KB 120x90 placeholder both fall through).
async function thumbBest(msg) {
  const id = SF.parseVideoId(String((msg && (msg.id || msg.url)) || '')) || String((msg && msg.id) || '');
  if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return { ok: false, error: 'bad video id' };
  let fallback = null;
  for (const size of SF.THUMB_SIZES) {
    const url = SF.pickThumbUrl(id, size);
    const r = await thumb({ url });
    if (!r.ok) continue;
    if (!SF.isPlaceholderThumb(r.bytes)) return { ok: true, url, size, bytes: r.bytes, contentType: r.contentType };
    if (!fallback) fallback = { ok: true, url, size, bytes: r.bytes, contentType: r.contentType, placeholder: true };
  }
  return fallback || { ok: false, error: 'no thumbnail found for ' + id };
}

// Base64 of an ArrayBuffer, chunked so String.fromCharCode never blows the call stack.
function base64FromArrayBuffer(buf) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) bin += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + CH, bytes.length)));
  return btoa(bin);
}

// The on-page thumbnail grabber (content/thumbgrab.js) needs the actual image BYTES to save
// with a custom filename and to copy to the clipboard. Content-script fetches to i.ytimg.com
// are CORS-blocked in Chrome, so the grabber falls back to this: we fetch here (host permission,
// no CORS for the service worker) and hand back a data: URL. maxres -> hq like popup.js.
async function thumbBlob(msg) {
  const id = SF.parseVideoId(String((msg && (msg.id || msg.url)) || '')) || String((msg && msg.id) || '');
  if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return { ok: false, error: 'bad video id' };
  for (const size of ['maxresdefault', 'hqdefault']) {
    const url = SF.pickThumbUrl(id, size);
    const { res, error } = await fetchWithTimeout(url, { method: 'GET' });
    if (error || !res || !res.ok) {
      try { if (res && res.body && res.body.cancel) await res.body.cancel(); } catch (_) { /* ignore */ }
      continue;
    }
    let buf;
    try { buf = await res.arrayBuffer(); } catch (_) { continue; }
    if (!buf || buf.byteLength < 1500) continue;   // 404 substitute / 120x90 placeholder
    const ct = res.headers.get('content-type') || 'image/jpeg';
    return { ok: true, dataUrl: 'data:' + ct + ';base64,' + base64FromArrayBuffer(buf), size, bytes: buf.byteLength, videoId: id };
  }
  return { ok: false, error: 'no thumbnail found for ' + id };
}

// Any image from YouTube's CDNs as a data URL (channel avatars for the tile composer).
// Restricted to the two hosts the manifest grants, so a page can't turn this into a proxy.
async function imageBlob(msg) {
  const url = String((msg && msg.url) || '');
  if (!/^https:\/\/(i\.ytimg\.com|yt3\.ggpht\.com|yt3\.googleusercontent\.com)\//.test(url)) return { ok: false, error: 'host not allowed' };
  const { res, error } = await fetchWithTimeout(url, { method: 'GET' });
  if (error || !res || !res.ok) {
    try { if (res && res.body && res.body.cancel) await res.body.cancel(); } catch (_) { /* ignore */ }
    return { ok: false, error: error || ('HTTP ' + (res && res.status)) };
  }
  let buf;
  try { buf = await res.arrayBuffer(); } catch (e) { return { ok: false, error: String(e) }; }
  if (!buf || buf.byteLength < 200) return { ok: false, error: 'empty image' };
  const ct = res.headers.get('content-type') || 'image/jpeg';
  return { ok: true, dataUrl: 'data:' + ct + ';base64,' + base64FromArrayBuffer(buf), bytes: buf.byteLength };
}

async function setSettings(msg) {
  const patch = (msg && msg.patch && typeof msg.patch === 'object') ? msg.patch : {};
  await api.storage.sync.set(patch);
  return { ok: true };
}

const handlers = {
  ping,
  channels,
  realtime,
  probe,
  'probe:get': probeGet,
  'probe:stats': probeStats,
  'probe:clear': probeClear,
  'ext-analytics': extAnalytics,
  thumb,
  thumbBest,
  thumbBlob,
  imageBlob,
  getSettings: async () => ({ ok: true, settings: await getSettings() }),
  setSettings
};

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const fn = msg && handlers[msg.type];
  if (!fn) { sendResponse({ ok: false, error: 'unknown message type: ' + (msg && msg.type) }); return false; }
  Promise.resolve()
    .then(() => fn(msg, sender))
    .then(res => sendResponse(res || { ok: true }))
    .catch(e => sendResponse({ ok: false, error: String((e && e.message) || e) }));
  return true; // async response
});

/* ------------------------------------------------------------------ alarms */

async function ensureAlarm(name, periodInMinutes) {
  try {
    const existing = await api.alarms.get(name);
    if (!existing) await api.alarms.create(name, { periodInMinutes, delayInMinutes: periodInMinutes });
  } catch (e) {
    console.warn(TAG, 'alarm', name, e);
  }
}

function scheduleAlarms() {
  ensureAlarm(PING_ALARM, 5);
  ensureAlarm(PRUNE_ALARM, 24 * 60);
}

// Drop expired hn:<handle> cache rows so storage.local does not grow forever.
async function pruneHandleCache() {
  try {
    const all = await api.storage.local.get(null);
    const now = Date.now();
    const dead = [];
    for (const [k, v] of Object.entries(all)) {
      if (!k.startsWith('hn:')) continue;
      const ts = v && Number(v.ts);
      const ttl = v && v.title ? SF.HANDLE_CACHE_TTL_MS : SF.HANDLE_NEG_TTL_MS;
      if (!ts || now - ts > ttl) dead.push(k);
    }
    if (dead.length) await api.storage.local.remove(dead);
  } catch (e) {
    console.warn(TAG, 'prune failed', e);
  }
}

api.alarms.onAlarm.addListener(alarm => {
  if (!alarm) return;
  if (alarm.name === PING_ALARM) ping().catch(() => {});
  else if (alarm.name === PRUNE_ALARM) pruneHandleCache();
});

api.runtime.onInstalled.addListener(() => {
  seedDefaults().then(() => { scheduleAlarms(); ping().catch(() => {}); });
});

api.runtime.onStartup.addListener(() => {
  scheduleAlarms();
  ping().catch(() => {});
});

// Keep the badge honest whenever the site URL / token changes.
api.storage.onChanged.addListener((changes, area) => {
  if (area !== 'sync') return;
  if (changes.siteUrl || changes.token) ping().catch(() => {});
});
