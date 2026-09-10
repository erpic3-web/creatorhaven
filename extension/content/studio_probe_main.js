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

    let enabled = true;           // toggled by studio.js: { source: "sf-probe-ctl", enabled }
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
        if (!enabled) return;
        let keys = [], hits = [];
        if (text && text.length && text.length < 8 * 1024 * 1024) {
          try {
            // youtubei responses are plain JSON (no XSSI prefix on Studio's endpoints), but
            // strip the classic ")]}'" guard just in case.
            const body = text.charCodeAt(0) === 41 && text.startsWith(")]}'") ? text.slice(4) : text;
            const parsed = JSON.parse(body);
            if (parsed && typeof parsed === 'object') {
              keys = Array.isArray(parsed) ? ['[array:' + parsed.length + ']'] : Object.keys(parsed).slice(0, 60);
              hits = collectHits(parsed);
              harvest(url, parsed);
            }
          } catch (_) { /* not JSON */ }
        }
        const msg = {
          source: 'sf-probe',
          url: pathOnly(url),
          status: Number(status) || 0,
          keys,
          size: text ? text.length : 0,
          hits,
          ts: Date.now()
        };
        if (FULL_RE.test(url) && text && text.length) {
          msg.full = true;
          msg.sample = text.length > FULL_CAP ? text.slice(0, FULL_CAP) : text;
          if (typeof reqBody === 'string' && reqBody) {
            msg.reqBody = reqBody.length > FULL_CAP ? reqBody.slice(0, FULL_CAP) : reqBody;
          }
        }
        window.postMessage(msg, location.origin);
      } catch (_) { /* never surface */ }
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
          if (enabled && url.indexOf(MATCH) !== -1 && p && typeof p.then === 'function') {
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
      const origOpen = XP.open, origSend = XP.send;
      XP.open = function (method, url) {
        try { this.__sfUrl = String(url); } catch (_) { /* ignore */ }
        return origOpen.apply(this, arguments);
      };
      XP.send = function (bodyArg) {
        try {
          const url = this.__sfUrl || '';
          const reqBody = (typeof bodyArg === 'string') ? bodyArg : '';
          if (enabled && url.indexOf(MATCH) !== -1) {
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
        if (!enabled) return;
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
