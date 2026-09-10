/* CreatorHaven — content/studio.js  (studio.youtube.com, isolated world, classic script, document_start)
 *
 * Features: confirmSignOut, studioDiscordButton, streamerMode (+ Alt+Shift+H), and the relay
 * that forwards the MAIN-world probe's postMessages (content/studio_probe_main.js) to bg.js.
 *
 * Studio is built from Polymer elements with REAL (open) shadow roots, unlike www.youtube.com:
 *   - element lookups go through deepQuery(), which descends into every shadowRoot,
 *   - injected UI is styled INLINE (a document stylesheet never reaches into a shadow tree),
 *   - the single MutationObserver is attached to every shadow root the walker meets.
 * Everything is try/catch-wrapped and idempotent, same contract as content/yt.js.
 */
(() => {
  'use strict';

  const api = globalThis.browser ?? globalThis.chrome;
  const SF = globalThis.SF;
  const TAG = '[SF studio]';
  if (!SF || !api || !api.runtime || !api.storage) {
    console.warn(TAG, 'runtime or lib/common.js missing, nothing to do');
    return;
  }

  /* ================================================================ infrastructure */

  let settings = SF.withDefaults({});
  let lastHref = location.href;
  const warned = new Set();
  const observedRoots = new WeakSet();
  let mo = null;                    // created in init(); observeRoot() is a no-op before that
  let pillRef = null;               // the Discord pill element
  let blurRootRef = null;           // cached #main-container

  function warn(scope, err) {
    const key = scope + ':' + (err && err.message ? err.message : String(err));
    if (warned.has(key)) return;
    warned.add(key);
    console.warn(TAG, scope, err);
  }

  function safe(scope, fn) {
    try { return fn(); } catch (e) { warn(scope, e); return undefined; }
  }

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

  function applyStyle(node, styles) {
    for (const k of Object.keys(styles)) node.style.setProperty(k, styles[k], 'important');
  }

  let toastTimer = 0;
  function toast(text) {
    safe('toast', () => {
      if (!document.body) return;
      let t = document.querySelector('.sf-toast');
      if (!t) { t = el('div', 'sf-toast sf-ui'); document.body.appendChild(t); }
      t.textContent = text;
      t.classList.add('sf-toast-show');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => t.classList.remove('sf-toast-show'), 2200);
    });
  }

  /* ---- shadow-aware DOM helpers ---- */

  // querySelector that also searches every open shadow root under `root`.
  function deepQuery(selector, root) {
    root = root || document;
    let direct = null;
    try { direct = root.querySelector(selector); } catch (_) { return null; }
    if (direct) return direct;
    for (const host of root.querySelectorAll('*')) {
      if (host.shadowRoot) {
        const r = deepQuery(selector, host.shadowRoot);
        if (r) return r;
      }
    }
    return null;
  }

  function observeRoot(root) {
    if (!mo || !root || observedRoots.has(root)) return;
    observedRoots.add(root);
    try { mo.observe(root, { childList: true, subtree: true, characterData: true }); }
    catch (e) { warn('observe', e); }
  }

  // Depth-first over elements + text nodes, descending into open shadow roots (and
  // registering each one with the observer). visit(node) === false skips that subtree.
  function walkNodes(root, visit, budget) {
    const stack = [root];
    let left = budget || 200000;
    while (stack.length && left-- > 0) {
      const node = stack.pop();
      if (visit(node) === false) continue;
      const type = node.nodeType;
      if (type !== 1 && type !== 9 && type !== 11) continue;
      if (type === 1 && node.shadowRoot) { observeRoot(node.shadowRoot); stack.push(node.shadowRoot); }
      for (let c = node.lastChild; c; c = c.previousSibling) stack.push(c);
    }
  }

  /* ================================================================ features */

  const features = [];
  const active = new Map();

  /* ---- 13. confirmSignOut ------------------------------------------------- */
  // NOTE: Studio's avatar menu is a cross-origin Google iframe we cannot script; this
  // guard covers sign-out links rendered in Studio's own DOM.
  function isSignOutTarget(path) {
    for (const node of path) {
      if (!node || node.nodeType !== 1) continue;
      if (node === document.body) return false;
      if (node.tagName === 'A' && /\/logout(\b|$)/.test(node.getAttribute('href') || '')) return true;
      if (node.matches && node.matches('a, button, [role="menuitem"], [role="link"], tp-yt-paper-item')) {
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

  /* ---- 14. studioDiscordButton ------------------------------------------- */
  // "Creator Ledger": a thin steel tab (1px zinc-800 border, 2px corners, uppercase sans tag).
  const PILL_STYLE = {
    display: 'inline-flex', 'align-items': 'center', height: '32px', padding: '0 12px',
    'margin-right': '12px', 'border-radius': '2px', 'text-decoration': 'none',
    background: '#1E2124', border: '1px solid #27272A', color: '#FFFFFF',
    font: '600 11px "Space Grotesk", "Inter", system-ui, Arial, sans-serif', 'text-transform': 'uppercase', 'letter-spacing': '.14em',
    'white-space': 'nowrap', 'align-self': 'center', cursor: 'pointer', transition: 'all .2s cubic-bezier(.16,1,.3,1)'
  };

  const FLOAT_STYLE = { position: 'fixed', top: '10px', right: '140px', 'z-index': '2147483000', 'box-shadow': '0 8px 24px rgba(0,0,0,.45)' };

  function findHeaderHost() {
    return deepQuery('#right-section') || deepQuery('ytcp-header #header') || deepQuery('#header');
  }

  // Puts the pill at the left edge of the header's right section, or floats it top-right
  // until that section exists (Studio renders its header shadow tree a beat after boot).
  function placePill(a, host) {
    if (host) {
      for (const k of Object.keys(FLOAT_STYLE)) a.style.removeProperty(k);
      a.classList.remove('sf-discord-floating');
      delete a.dataset.sfFloating;
      host.insertBefore(a, host.firstChild);
      return true;
    }
    if (!document.body) return false;
    a.classList.add('sf-discord-floating');
    a.dataset.sfFloating = '1';
    applyStyle(a, FLOAT_STYLE);
    document.body.appendChild(a);
    return true;
  }

  features.push({
    name: 'studioDiscordButton', key: 'studioDiscordButton',
    run() {
      const url = String(settings.discordUrl || '').trim();
      if (!/^https?:\/\//i.test(url)) { if (pillRef) { pillRef.remove(); pillRef = null; } return; }
      if (pillRef && pillRef.isConnected) {
        if (pillRef.href !== url) pillRef.href = url;
        if (pillRef.dataset.sfFloating) { const host = findHeaderHost(); if (host) placePill(pillRef, host); }
        return;
      }
      if (pillRef) pillRef.remove();
      const a = el('a', 'sf-discord-pill sf-ui', 'Discord');
      a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.title = 'Open Discord';
      applyStyle(a, PILL_STYLE);
      if (placePill(a, findHeaderHost())) pillRef = a;
    },
    off() { if (pillRef) { pillRef.remove(); pillRef = null; } }
  });

  /* ---- 15. streamerMode --------------------------------------------------- */
  // Wraps every 2+-digit number (see SF.numberBlurRegex) in a blurred span. Idempotent by
  // construction: after a pass, digit runs live only inside data-sf-blur spans (skipped) and
  // the remaining plain text nodes contain no matching run.
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'INPUT', 'SELECT', 'OPTION', 'NOSCRIPT', 'IFRAME', 'SVG', 'CANVAS', 'VIDEO', 'AUDIO']);

  function skipElement(node) {
    const tag = String(node.tagName || '').toUpperCase();
    if (SKIP_TAGS.has(tag)) return true;
    if (node.hasAttribute('data-sf-blur') || node.classList.contains('sf-ui')) return true;
    if (node.isContentEditable) return true;
    return false;
  }

  function blurTextNode(tn) {
    const text = tn.nodeValue;
    if (!text || !SF.hasNumberRun(text)) return;
    const parent = tn.parentNode;
    if (!parent) return;
    const segs = SF.segmentNumbers(text);
    if (!segs.some(s => s.blur)) return;
    const frag = document.createDocumentFragment();
    for (const s of segs) {
      if (!s.blur) { frag.appendChild(document.createTextNode(s.text)); continue; }
      const span = el('span', 'sf-blur', s.text);
      span.setAttribute('data-sf-blur', '1');
      span.style.setProperty('filter', 'blur(6px)', 'important');   // works inside shadow roots too
      frag.appendChild(span);
    }
    parent.replaceChild(frag, tn);
  }

  function blurRoot() {
    if (blurRootRef && blurRootRef.isConnected) return blurRootRef;
    blurRootRef = deepQuery('#main-container') || document.body || null;
    return blurRootRef;
  }

  function applyBlur() {
    const root = blurRoot();
    if (!root) return;
    const pending = [];
    walkNodes(root, node => {
      if (node.nodeType === 1) return !skipElement(node);
      if (node.nodeType === 3) pending.push(node);
      return true;
    }, 150000);
    for (const tn of pending) blurTextNode(tn);
  }

  function removeBlur() {
    const spans = [];
    walkNodes(document.documentElement, node => {
      if (node.nodeType === 1 && node.hasAttribute('data-sf-blur')) { spans.push(node); return false; }
      return true;
    }, 300000);
    const parents = new Set();
    for (const s of spans) {
      const p = s.parentNode;
      if (!p) continue;
      p.replaceChild(document.createTextNode(s.textContent), s);
      parents.add(p);
    }
    for (const p of parents) { try { p.normalize(); } catch (_) { /* ignore */ } }
  }

  features.push({ name: 'streamerMode', key: 'streamerMode', run: applyBlur, off: removeBlur });

  function installHotkey() {
    document.addEventListener('keydown', e => {
      if (!(e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey)) return;
      if (e.code !== 'KeyH' && String(e.key || '').toUpperCase() !== 'H') return;
      e.preventDefault();
      safe('streamerMode:hotkey', () => {
        const next = !settings.streamerMode;
        settings.streamerMode = next;
        Promise.resolve(api.storage.sync.set({ streamerMode: next })).catch(err => warn('streamerMode:save', err));
        toast('Streamer mode ' + (next ? 'ON' : 'OFF'));
        scheduleRun();
      });
    }, true);
  }

  /* ---- 16. studioProbe relay --------------------------------------------- */
  function installProbeRelay() {
    window.addEventListener('message', e => {
      if (e.source !== window || !e.data || e.data.source !== 'sf-probe') return;
      if (!settings.studioProbe) return;
      safe('studioProbe', () => {
        const d = e.data;
        const entry = {
          url: String(d.url || '').slice(0, 300),
          status: Number(d.status) || 0,
          keys: Array.isArray(d.keys) ? d.keys.slice(0, 60).map(String) : [],
          size: Number(d.size) || 0,
          ts: Number(d.ts) || Date.now(),
          hits: Array.isArray(d.hits) ? d.hits.slice(0, 100) : []
        };
        if (d.full && typeof d.sample === 'string') {
          entry.full = true;
          entry.sample = d.sample.slice(0, 60000);
          if (typeof d.reqBody === 'string') entry.reqBody = d.reqBody.slice(0, 60000);
        }
        send({ type: 'probe', entry });
        if (entry.hits.length) {
          send({ type: 'realtime', channelId: SF.channelIdFromPath(location.pathname), hits: entry.hits, url: entry.url, ts: entry.ts });
        }
      });
    });
    // Harvested per-channel numbers (parsed from Studio's own responses) -> site ingest.
    window.addEventListener('message', e => {
      if (e.source !== window || !e.data || e.data.source !== 'sf-harvest') return;
      if (!settings.studioProbe) return;
      const ch = e.data.channel;
      if (ch && ch.channel_id) safe('harvest', () => send({ type: 'ext-analytics', payload: { channels: [ch] } }));
    });
  }

  // Tell the MAIN-world probe whether it should report at all (it defaults to on).
  function pushProbeCtl() {
    try { window.postMessage({ source: 'sf-probe-ctl', enabled: !!settings.studioProbe }, location.origin); }
    catch (e) { warn('probeCtl', e); }
  }

  /* ================================================================ engine */

  function runAll() {
    for (const f of features) {
      if (settings[f.key]) {
        safe(f.name, () => f.run && f.run());
        active.set(f.name, true);
      } else if (active.get(f.name)) {
        active.set(f.name, false);
        safe(f.name + ':off', () => f.off && f.off());
      }
    }
  }

  const scheduleRun = SF.debounce(() => safe('runAll', runAll), 400);

  async function loadSettings() {
    try { settings = SF.withDefaults(await api.storage.sync.get(SF.SF_DEFAULTS)); }
    catch (e) { warn('settings', e); }
  }

  function installSettingsSync() {
    api.storage.onChanged.addListener((changes, area) => {
      if (area !== 'sync') return;
      safe('onChanged', () => {
        const patch = {};
        for (const [k, c] of Object.entries(changes)) patch[k] = c.newValue === undefined ? SF.SF_DEFAULTS[k] : c.newValue;
        settings = SF.withDefaults(Object.assign({}, settings, patch));
        if ('studioProbe' in patch) pushProbeCtl();
        if ('discordUrl' in patch && pillRef) { pillRef.remove(); pillRef = null; }
      });
      scheduleRun();
    });
  }

  async function init() {
    installSignOutGuard();
    installHotkey();
    installProbeRelay();
    installSettingsSync();
    await loadSettings();
    pushProbeCtl();
    setTimeout(pushProbeCtl, 1000);   // in case the MAIN-world listener was not up yet
    setTimeout(pushProbeCtl, 4000);
    mo = new MutationObserver(scheduleRun);
    observeRoot(document.documentElement);   // document_start: body may not exist yet
    scheduleRun();
    const startedAt = Date.now();
    setInterval(() => {
      if (location.href !== lastHref) { lastHref = location.href; scheduleRun(); }
      // Header shadow tree not up at the first pass: keep trying to re-home the pill for a minute.
      else if (pillRef && pillRef.dataset.sfFloating && Date.now() - startedAt < 60000) scheduleRun();
    }, 1000);
  }

  init().catch(e => warn('init', e));
})();
