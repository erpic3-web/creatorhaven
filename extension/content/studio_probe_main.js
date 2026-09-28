/* CreatorHaven — content/studio_probe_main.js  (studio.youtube.com, MAIN world, document_start)
 *
 * Runs inside the page's own JavaScript world so it can wrap window.fetch and
 * XMLHttpRequest — the isolated world only sees copies of those. For every response whose
 * URL contains "/youtubei/v1/" it posts a small summary to the isolated world
 * (content/studio.js) via window.postMessage({ source: "sf-probe", ... }).
 *
 * Contract with Studio: NEVER break it. Every line is inside try/catch, the original
 * Response object is returned untouched (we read a clone()), and the wrapper is a pure
 * pass-through when disabled. This file is deliberately self-contained (no lib/common.js —
 * loading it here would leak a global into the page); test/run.js asserts the hit-key regex
 * below stays identical to SF.probeHitKeyRegex.
 */
(() => {
  'use strict';
  try {
    if (window.__sfProbeInstalled) return;
    Object.defineProperty(window, '__sfProbeInstalled', { value: true, enumerable: false, configurable: false });

    let enabled = true;           // toggled by studio.js: { source: "sf-probe-ctl", enabled, capture, latestPlus }
    let capture = false;          // FULL request/response mirroring to the site (developer shape capture): off by default
    const MAX_DEPTH = 8, MAX_NODES = 5000, MAX_HITS = 200;
    const HIT_RE = /realtime|last48|last60|fortyEight|sixtyMin|engaged|monetiz|adBreak|midroll/i;
    // Endpoints whose FULL response we mirror to the site (capped) so the build has the
    // real request shapes without anyone exporting a log by hand.
    // Broad during the one-time shape-capture pass: every Studio youtubei call, full
    // req+resp. (Narrowed to just the puller's endpoints once the puller ships.)
    const FULL_RE = /\/youtubei\/v1\//i;
    const FULL_CAP = 200000;
    const MATCH = '/youtubei/v1/';

    window.addEventListener('message', e => {
      try {
        if (e.source !== window || !e.data || e.data.source !== 'sf-probe-ctl') return;
        enabled = !!e.data.enabled;
        if ('capture' in e.data) capture = !!e.data.capture;
        if ('latestPlus' in e.data) latestPlus = !!e.data.latestPlus;
      } catch (_) { /* ignore */ }
    });

    function pathOnly(url) {
      try { return new URL(String(url), location.href).pathname; }
      catch (_) { return String(url || '').split(/[?#]/)[0]; }
    }

    // Same walk as SF.collectProbeHits (lib/common.js) — kept in sync by the tests.
    function collectHits(value) {
      const hits = [];
      let nodes = 0;
      const stack = [{ v: value, path: '$', depth: 0 }];
      while (stack.length) {
        const cur = stack.pop();
        const v = cur.v;
        if (v === null || typeof v !== 'object') continue;
        if (++nodes > MAX_NODES) break;
        if (cur.depth >= MAX_DEPTH) continue;
        const isArr = Array.isArray(v);
        const keys = isArr ? null : Object.keys(v);
        const len = isArr ? v.length : keys.length;
        for (let i = 0; i < len; i++) {
          const k = isArr ? i : keys[i];
          const child = v[k];
          const childPath = isArr ? cur.path + '[' + k + ']' : cur.path + '.' + k;
          if (!isArr && HIT_RE.test(k)) {
            let val;
            if (typeof child === 'number') val = child;
            else if (typeof child === 'string' && child.length <= 40) val = child;
            const hit = { path: childPath, key: k, type: Array.isArray(child) ? 'array' : (child === null ? 'null' : typeof child) };
            if (val !== undefined) hit.value = val;
            hits.push(hit);
            if (hits.length >= MAX_HITS) return hits;
          }
          if (child && typeof child === 'object') stack.push({ v: child, path: childPath, depth: cur.depth + 1 });
        }
      }
      return hits;
    }

    // --- Harvest: read the numbers straight out of Studio's own responses (no replay,
    // no auth). Works for delegated channels because Studio already fetched them. ---
    function _cid() {
      try { const m = location.pathname.match(/\/channel\/(UC[0-9A-Za-z_-]+)/); return m ? m[1] : null; }
      catch (_) { return null; }
    }
    function _n(x) {
      const v = (typeof x === 'string') ? parseInt(x, 10) : x;
      return (typeof v === 'number' && isFinite(v)) ? v : null;
    }
    function _thumb(td) {
      try { const t = (td && td.thumbnails) || []; return t.length ? t[t.length - 1].url : null; }
      catch (_) { return null; }
    }
    function _pushChannel(rec) {
      try { window.postMessage({ source: 'sf-harvest', channel: rec }, location.origin); } catch (_) { /* ignore */ }
    }
    function harvest(url, parsed) {
      try {
        if (!parsed || typeof parsed !== 'object') return;
        if (/get_creator_channels/.test(url) && Array.isArray(parsed.channels)) {
          for (const c of parsed.channels) {
            const m = c.metric || {};
            if (!c.channelId) continue;
            _pushChannel({ channel_id: c.channelId, title: c.title,
              stats: { subscribers: _n(m.subscriberCount), views: _n(m.totalVideoViewCount), videos: _n(m.videoCount) } });
          }
        } else if (/get_creator_videos/.test(url) && Array.isArray(parsed.videos)) {
          const byc = {};
          for (const v of parsed.videos) {
            const cid = v.channelId || _cid();
            if (!cid || !v.videoId) continue;
            const pm = v.publicMetrics || {};
            (byc[cid] = byc[cid] || []).push({
              video_id: v.videoId, title: v.title, thumb: _thumb(v.thumbnailDetails),
              views: _n(pm.externalViewCount) != null ? _n(pm.externalViewCount) : _n(pm.viewCount),
              engaged_views: _n(pm.viewCount), likes: _n(pm.likeCount), comments: _n(pm.commentCount),
              is_short: (v.contentType && /SHORT/i.test(v.contentType)) ? 1 : 0
            });
          }
          for (const cid in byc) _pushChannel({ channel_id: cid, videos: byc[cid] });
        }
      } catch (_) { /* never surface */ }
    }

    function report(url, status, text, reqBody) {
      try {
        let keys = [], hits = [];
        if (text && text.length && text.length < 8 * 1024 * 1024) {
          try {
            // youtubei responses are plain JSON (no XSSI prefix on Studio's endpoints), but
            // strip the classic ")]}'" guard just in case.
            const body = text.charCodeAt(0) === 41 && text.startsWith(")]}'") ? text.slice(4) : text;
            const parsed = JSON.parse(body);
            if (parsed && typeof parsed === 'object') {
              if (/get_channel_dashboard/.test(url)) onDashboard(parsed);
              if (/get_creator_videos/.test(url)) noteAdSettings(parsed);
              if (!enabled) return;
              keys = Array.isArray(parsed) ? ['[array:' + parsed.length + ']'] : Object.keys(parsed).slice(0, 60);
              hits = collectHits(parsed);
              harvest(url, parsed);
            }
          } catch (_) { /* not JSON */ }
        }
        if (!enabled) return;
        const msg = {
          source: 'sf-probe',
          url: pathOnly(url),
          status: Number(status) || 0,
          keys,
          size: text ? text.length : 0,
          hits,
          ts: Date.now()
        };
        if (capture && FULL_RE.test(url) && text && text.length) {
          msg.full = true;
          msg.sample = text.length > FULL_CAP ? text.slice(0, FULL_CAP) : text;
          if (typeof reqBody === 'string' && reqBody) {
            msg.reqBody = reqBody.length > FULL_CAP ? reqBody.slice(0, FULL_CAP) : reqBody;
          }
        }
        window.postMessage(msg, location.origin);
      } catch (_) { /* never surface */ }
    }


    /* ---- replaying Studio's own calls (for the "Latest video performance" extras) ----
       We never build credentials. We reuse the exact headers and request `context` Studio
       itself sent on its most recent youtubei call (so a delegated channel stays delegated).
       They live only in this page's memory and are never sent anywhere else. */
    const AUTH_KEYS = /^(authorization|x-goog-authuser|x-goog-pageid|x-origin|x-youtube-client-name|x-youtube-client-version|x-goog-visitor-id|x-youtube-delegation-context)$/i;
    let lastAuth = null, lastCtx = null, lastQuery = '';
    function headerObj(h) {
      const o = {};
      try {
        if (!h) return o;
        if (typeof h.forEach === 'function' && !Array.isArray(h)) h.forEach((v, k) => { o[k] = v; });
        else if (Array.isArray(h)) h.forEach((p) => { if (p && p.length === 2) o[p[0]] = p[1]; });
        else Object.keys(h).forEach((k) => { o[k] = h[k]; });
      } catch (_) { /* ignore */ }
      return o;
    }
    function noteAuth(h, url, body) {
      try {
        const keep = {};
        for (const k of Object.keys(h || {})) if (AUTH_KEYS.test(k)) keep[k] = String(h[k]);
        if (Object.keys(keep).some((k) => /^authorization$/i.test(k))) lastAuth = { headers: keep, t: Date.now() };
        const q = String(url || '').split('?')[1];
        if (q) lastQuery = '?' + q;
        if (typeof body === 'string' && body.length < 600000 && body.indexOf('"context"') !== -1) {
          const j = JSON.parse(body);
          if (j && j.context && typeof j.context === 'object') lastCtx = j.context;
        }
      } catch (_) { /* ignore */ }
    }
    async function studioCall(path, payload) {
      if (!lastAuth || !lastCtx) throw new Error('waiting for Studio');
      const headers = Object.assign({ 'Content-Type': 'application/json' }, lastAuth.headers);
      const r = await origFetch.call(window, location.origin + '/youtubei/v1/' + path + (lastQuery || '?alt=json'), {
        method: 'POST', credentials: 'include', headers, body: JSON.stringify(Object.assign({ context: lastCtx }, payload))
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }
    const num = (x) => { const v = typeof x === 'string' ? Number(x) : x; return (typeof v === 'number' && isFinite(v)) ? v : null; };
    function post(msg) { try { window.postMessage(msg, location.origin); } catch (_) { /* ignore */ } }

    // Studio's own ad settings for a video -> the Ad Placer's red-slot remover (isolated world)
    function noteAdSettings(parsed) {
      for (const v of (parsed && parsed.videos) || []) {
        const mm = v && v.adSettings && v.adSettings.adBreaks && v.adSettings.adBreaks.manualMidrollPlacements;
        if (!mm) continue;
        const ms = [];
        for (const d of mm.details || []) {
          if (!d || !d.isManualMidrollDisruptive) continue;
          const p = d.placementTimesMillis;
          (Array.isArray(p) ? p : [p]).forEach((x) => { const n = num(x); if (n != null) ms.push(n); });
        }
        post({ source: 'sf-adbreaks', videoId: v.videoId, disruptiveMs: ms });
      }
    }

    /* ---- "Latest video performance": engaged views + the rank against ALL uploads ----
       Studio's card ranks the newest video against the last 10 same-format uploads by views at
       the SAME AGE. We rank it against every public upload of that format: first with exact
       same-age windows (checked against the card's own numbers for the 10 it shows), else with
       whole days since publish, which YouTube keeps for every video. */
    let latestPlus = true;
    const rankCache = new Map();   // videoId|mode|span -> views (a finished window never changes)
    const listCache = new Map();   // channel|format -> {t, vids}
    let latestKey = '';
    function walk(o, visit, depth) {
      if (!o || typeof o !== 'object' || depth > 12) return;
      if (visit(o) === true) return;
      for (const k in o) { const v = o[k]; if (v && typeof v === 'object') walk(v, visit, (depth || 0) + 1); }
    }
    function onDashboard(parsed) {
      if (!latestPlus) return;
      let data = null; const pubs = {};
      walk(parsed, (o) => {
        if (o.entitySnapshotCardData && o.entitySnapshotCardData.video) { data = o.entitySnapshotCardData; }
        if (o.videoId && o.timePublishedSeconds) pubs[o.videoId] = { pub: num(o.timePublishedSeconds), channelId: o.channelId };
      }, 0);
      const vid = data && data.video && data.video.externalVideoId;
      if (!vid || !pubs[vid] || !pubs[vid].pub) return;
      const ranking = ((data.ranking && data.ranking.entities) || []).map((e) => ({ videoId: e.entity && e.entity.videoId, views: num(e.value && e.value.double) }));
      const channelId = pubs[vid].channelId || ((location.pathname.match(/\/channel\/(UC[\w-]{22})/) || [])[1]);
      const key = vid + ':' + Math.floor(Date.now() / 60000);
      if (key === latestKey) return;            // at most once a minute per video
      latestKey = key;
      runLatest({ vid, channelId, pubS: pubs[vid].pub, format: String(data.video.videoFormat || ''), ranking }).catch(() => {});
    }
    async function runLatest(info, attempt) {
      const out = { source: 'sf-latest', videoId: info.vid, ageS: Math.max(0, Date.now() / 1000 - info.pubS), shorts: /SHORT/i.test(info.format) };
      try {
        const j = await studioCall('creator/get_creator_videos', { videoIds: [info.vid], mask: { videoId: true, publicMetrics: { all: true } } });
        const pm = (((j && j.videos) || [])[0] || {}).publicMetrics || {};
        out.engaged = num(pm.viewCount); out.views = num(pm.externalViewCount);
      } catch (e) {
        if (/waiting for Studio/.test(String(e && e.message)) && (attempt || 0) < 6) { setTimeout(() => runLatest(info, (attempt || 0) + 1).catch(() => {}), 2500); return; }
        out.engagedError = String((e && e.message) || e);
      }
      post(out);
      try { Object.assign(out, await rankAll(info)); } catch (e) { out.rankError = String((e && e.message) || e); }
      post(out);
    }
    async function listUploads(channelId, shorts) {
      const ck = channelId + '|' + (shorts ? 'S' : 'L');
      const c = listCache.get(ck);
      if (c && Date.now() - c.t < 5 * 60000) return c.vids;
      const typeOp = shorts ? { contentTypeIs: { value: 'CREATOR_CONTENT_TYPE_SHORTS' } }
        : { not: { operand: { contentTypeIs: { value: 'CREATOR_CONTENT_TYPE_SHORTS' } } } };
      const filter = { and: { operands: [{ channelIdIs: { value: channelId } }, { videoOriginIs: { value: 'VIDEO_ORIGIN_UPLOAD' } }, typeOp] } };
      const vids = [], seen = new Set();
      let token = null;
      for (let page = 0; page < 12; page++) {           // up to 600 uploads
        const body = { filter, order: 'VIDEO_ORDER_DISPLAY_TIME_DESC', pageSize: 50,
          mask: { videoId: true, timePublishedSeconds: true, privacy: true, contentType: true } };
        if (token) body.pageToken = token;
        const j = await studioCall('creator/list_creator_videos', body);
        const got = (j && j.videos) || [];
        let fresh = 0;
        for (const v of got) {
          if (!v || !v.videoId || seen.has(v.videoId)) continue;
          seen.add(v.videoId); fresh++;
          if (/PUBLIC/.test(String(v.privacy || 'PUBLIC'))) vids.push({ videoId: v.videoId, pub: num(v.timePublishedSeconds) });
        }
        token = j && j.nextPageToken;
        if (!token || !fresh) break;
      }
      listCache.set(ck, { t: Date.now(), vids });
      return vids;
    }
    const dayId = (s) => { const d = new Date(s * 1000); return d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate(); };
    async function windowViews(channelId, list, mode, span) {
      const out = {}, need = [];
      for (const v of list) { const ck = v.videoId + '|' + mode + '|' + span; if (rankCache.has(ck)) out[v.videoId] = rankCache.get(ck); else need.push(v); }
      for (let i = 0; i < need.length; i += 25) {
        const chunk = need.slice(i, i + 25);
        const nodes = chunk.map((v) => ({ key: 'v_' + v.videoId, value: { query: {
          dimensions: [], metrics: [{ type: 'EXTERNAL_VIEWS' }],
          restricts: [{ dimension: { type: 'USER' }, inValues: [channelId] }, { dimension: { type: 'VIDEO' }, inValues: [v.videoId] }],
          orders: [],
          timeRange: mode === 'exact'
            ? { unixTimeRange: { inclusiveStart: String(Math.floor(v.pub)), exclusiveEnd: String(Math.floor(v.pub + span)) } }
            : { dateIdRange: { inclusiveStart: dayId(v.pub), exclusiveEnd: dayId(v.pub + span * 86400) } },
          currency: 'USD', returnDataInNewFormat: true, limitedToBatchedData: false } } }));
        const j = await studioCall('yta_web/join', { nodes, connectors: [], allowFailureResultNodes: true, trackingLabel: 'web_explore_channel' });
        for (const r of (j && j.results) || []) {
          const vid = String(r.key || '').replace(/^v_/, '');
          const tbl = r.value && r.value.resultTable;
          if (!tbl) continue;
          const col = (tbl.metricColumns || [])[0];
          const vals = col && col.counts && col.counts.values;
          const n = vals && vals.length ? num(vals[0]) : 0;
          if (n == null) continue;
          out[vid] = n;
          if (mode === 'days' || n > 0) rankCache.set(vid + '|' + mode + '|' + span, n);
        }
      }
      return out;
    }
    async function rankAll(info) {
      const shorts = /SHORT/i.test(info.format);
      const all = (await listUploads(info.channelId, shorts)).filter((v) => v.pub);
      const peers = all.filter((v) => v.videoId !== info.vid && v.pub < info.pubS);
      if (!peers.length) return { rankUnavailable: 'no-peers' };
      const T = Math.floor(Date.now() / 1000 - info.pubS);
      const me = { videoId: info.vid, pub: info.pubS };
      // 1) exact: each video's first T seconds, trusted only if it reproduces the card's own numbers
      try {
        const ex = await windowViews(info.channelId, peers.concat([me]), 'exact', T);
        const checks = info.ranking.filter((r) => r.videoId && r.videoId !== info.vid && r.views != null && ex[r.videoId] != null);
        const agree = checks.filter((r) => Math.abs(ex[r.videoId] - r.views) <= Math.max(3, r.views * 0.2)).length;
        const covered = peers.filter((p) => ex[p.videoId] != null).length;
        if (checks.length >= 2 && agree >= Math.ceil(checks.length * 0.7) && covered >= peers.length * 0.8) {
          const mine = ex[info.vid] != null ? ex[info.vid] : ((info.ranking.find((r) => r.videoId === info.vid) || {}).views);
          if (mine != null) return Object.assign(rankOf(mine, peers.map((p) => ex[p.videoId])), { mode: 'exact', spanS: T });
        }
      } catch (_) { /* fall through to whole days */ }
      // 2) whole days since publish
      const D = Math.floor(T / 86400);
      if (D < 1) return { rankUnavailable: 'first-day', total: peers.length + 1 };
      const dv = await windowViews(info.channelId, peers.concat([me]), 'days', D);
      if (dv[info.vid] == null) return { rankUnavailable: 'no-data', total: peers.length + 1 };
      return Object.assign(rankOf(dv[info.vid], peers.map((p) => dv[p.videoId])), { mode: 'days', spanDays: D });
    }
    function rankOf(mine, values) {                   // same rule as SF.rankAmong (lib/common.js)
      const vals = values.filter((v) => typeof v === 'number' && isFinite(v));
      return { rank: 1 + vals.filter((v) => v > mine).length, of: vals.length + 1, mine };
    }

    /* ---- fetch ---- */
    const origFetch = window.fetch;
    if (typeof origFetch === 'function') {
      const wrapped = function (input, init) {
        const p = origFetch.apply(this, arguments);
        try {
          const url = typeof input === 'string' ? input : (input && typeof input === 'object' && input.url) ? input.url : String(input || '');
          const reqBody = (init && typeof init.body === 'string') ? init.body
            : (input && typeof input === 'object' && typeof input.body === 'string') ? input.body : '';
          if (url.indexOf(MATCH) !== -1) noteAuth(headerObj((init && init.headers) || (input && typeof input === 'object' && input.headers)), url, reqBody);
          if ((enabled || latestPlus) && url.indexOf(MATCH) !== -1 && p && typeof p.then === 'function') {
            p.then(res => {
              try {
                if (!res || typeof res.clone !== 'function') return;
                res.clone().text().then(t => report(res.url || url, res.status, t, reqBody)).catch(() => {});
              } catch (_) { /* ignore */ }
            }).catch(() => {});
          }
        } catch (_) { /* ignore */ }
        return p;   // the ORIGINAL promise / Response, untouched
      };
      try { Object.defineProperty(wrapped, 'name', { value: 'fetch' }); } catch (_) { /* ignore */ }
      window.fetch = wrapped;
    }

    /* ---- XMLHttpRequest ---- */
    const XP = XMLHttpRequest && XMLHttpRequest.prototype;
    if (XP && typeof XP.open === 'function' && typeof XP.send === 'function') {
      const origOpen = XP.open, origSend = XP.send, origSetHdr = XP.setRequestHeader;
      if (typeof origSetHdr === 'function') {
        XP.setRequestHeader = function (k, v) {
          try { (this.__sfHdrs = this.__sfHdrs || {})[k] = v; } catch (_) { /* ignore */ }
          return origSetHdr.apply(this, arguments);
        };
      }
      XP.open = function (method, url) {
        try { this.__sfUrl = String(url); } catch (_) { /* ignore */ }
        return origOpen.apply(this, arguments);
      };
      XP.send = function (bodyArg) {
        try {
          const url = this.__sfUrl || '';
          const reqBody = (typeof bodyArg === 'string') ? bodyArg : '';
          if (url.indexOf(MATCH) !== -1) noteAuth(this.__sfHdrs, url, reqBody);
          if ((enabled || latestPlus) && url.indexOf(MATCH) !== -1) {
            this.addEventListener('load', () => {
              try {
                let text = '';
                const rt = this.responseType;
                if (rt === '' || rt === 'text') text = this.responseText;
                else if (rt === 'json' && this.response) text = JSON.stringify(this.response);
                report(this.responseURL || url, this.status, text, reqBody);
              } catch (_) { /* ignore */ }
            });
          }
        } catch (_) { /* ignore */ }
        return origSend.apply(this, arguments);
      };
    }

    /* ---- one-time page-context snapshot (where the channel switcher's list + the
       per-channel delegation context live) so the puller can enumerate all channels ---- */
    function snapCfg() {
      try {
        if (!enabled || !capture) return;
        const src = (window.ytcfg && window.ytcfg.data_) ? window.ytcfg.data_ : null;
        if (!src) return;
        const pick = {};
        for (const k of Object.keys(src)) {
          if (/channel|delegat|account|innertube|session|creator|datasync|pageid|hats/i.test(k)) {
            try { pick[k] = src[k]; } catch (_) { /* getter threw */ }
          }
        }
        const text = JSON.stringify(pick);
        if (!text || text.length < 3) return;
        window.postMessage({
          source: 'sf-probe', url: 'sf://ytcfg', status: 200,
          keys: Object.keys(pick).slice(0, 60), size: text.length, hits: [], ts: Date.now(),
          full: true, sample: text.length > FULL_CAP ? text.slice(0, FULL_CAP) : text
        }, location.origin);
      } catch (_) { /* never surface */ }
    }
    setTimeout(snapCfg, 2500);
    setTimeout(snapCfg, 9000);
  } catch (_) {
    /* installing the probe failed: Studio keeps its native fetch/XHR */
  }
})();
