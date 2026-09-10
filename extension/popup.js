/* CreatorHaven — popup.js. Reads/writes settings in storage.sync (saved on every change),
 * tests the site connection through bg.js, and exposes the Studio probe log. */
(() => {
  'use strict';

  const api = globalThis.browser ?? globalThis.chrome;
  const SF = globalThis.SF;
  const TAG = '[SF popup]';
  const $ = s => document.querySelector(s);
  const $$ = s => Array.from(document.querySelectorAll(s));

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
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;opacity:0;left:-9999px';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch (_) { return false; }
  }

  let savedTimer = 0;
  function flash(text) {
    const s = $('#saved');
    s.textContent = text || 'Saved';
    s.classList.add('show');
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => s.classList.remove('show'), 1400);
  }

  function setStatus(text, kind) {
    const st = $('#connStatus');
    if (!st) return;                       // connection UI is stripped from this build
    st.textContent = text;
    st.className = 'status ' + (kind || 'muted');
  }

  function refreshDeps() {
    for (const box of $$('[data-dep]')) {
      const dep = $('[data-key="' + box.dataset.dep + '"]');
      box.classList.toggle('off', !(dep && dep.checked));
    }
  }

  function render(s, raw) {
    for (const inp of $$('[data-key]')) {
      const k = inp.dataset.key;
      if (inp.type === 'checkbox') inp.checked = !!s[k];
      else inp.value = s[k] == null ? '' : String(s[k]);
    }
    $('#pwVal').textContent = String(s.playerWidthPct);
    $('#cfVal').textContent = String(s.commentsFontPx);
    $('#favCount').textContent = String(Array.isArray(raw.favoritesList) ? raw.favoritesList.length : 0);
    refreshDeps();
  }

  async function load() {
    const raw = await api.storage.sync.get(Object.assign({ favoritesList: [] }, SF.SF_DEFAULTS));
    render(SF.withDefaults(raw), raw);
    refreshProbe();
  }

  function valueOf(inp) {
    if (inp.type === 'checkbox') return inp.checked;
    if (inp.type === 'range' || inp.type === 'number') return Number(inp.value);
    let v = inp.value.trim();
    if (inp.dataset.key === 'siteUrl') v = SF.normalizeSiteUrl(v);
    return v;
  }

  async function save(inp) {
    const k = inp.dataset.key;
    const v = valueOf(inp);
    try {
      await api.storage.sync.set({ [k]: v });
      flash();
    } catch (e) {
      console.warn(TAG, 'save failed', e);
      flash('Save failed');
    }
    if (k === 'siteUrl' && inp.value !== v) inp.value = v;
    refreshDeps();
  }

  async function refreshProbe() {
    const box = $('#probeStats');
    if (!box) return;                      // probe UI is stripped from this build
    const r = await send({ type: 'probe:stats' });
    if (!r.ok) { box.textContent = 'probe log: unavailable (' + (r.error || '?') + ')'; return; }
    let text = r.entries + ' endpoints · ' + r.total + ' responses · ' + r.hits + ' hits';
    if (r.lastSeen) text += ' · last ' + new Date(r.lastSeen).toLocaleTimeString();
    box.textContent = text;
  }

  async function testConnection() {
    const siteInp = $('[data-key="siteUrl"]');
    const tokInp = $('[data-key="token"]');
    const url = SF.normalizeSiteUrl(siteInp.value);
    siteInp.value = url;
    let origin;
    try { origin = new URL(url).origin; } catch (_) { setStatus('That is not a valid URL', 'bad'); return; }
    // A non-local site needs a host permission; the request must happen inside the click.
    if (!/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(origin) && api.permissions && api.permissions.request) {
      try {
        const granted = await api.permissions.request({ origins: [origin + '/*'] });
        if (!granted) { setStatus('Permission for ' + origin + ' was not granted', 'bad'); return; }
      } catch (e) { console.warn(TAG, 'permission request', e); }
    }
    setStatus('Testing…', 'muted');
    try { await api.storage.sync.set({ siteUrl: url, token: tokInp.value.trim() }); } catch (_) { /* ping still runs */ }
    const r = await send({ type: 'ping' });
    if (r.ok) setStatus('Connected: ' + r.app + ' v' + r.version, 'good');
    else setStatus('Failed: ' + (r.error || 'unknown error') + (r.url ? ' (' + r.url + ')' : ''), 'bad');
  }

  // ---- Thumbnail downloader (Ostendo-style: any video -> every resolution) ----
  const THUMB_RES = [
    { key: 'maxresdefault', label: 'Max', dim: '1280×720' },
    { key: 'sddefault', label: 'SD', dim: '640×480' },
    { key: 'hqdefault', label: 'HQ', dim: '480×360' },
    { key: 'mqdefault', label: 'MQ', dim: '320×180' },
    { key: 'default', label: 'S', dim: '120×90' },
  ];
  function parseVideoId(s) {
    s = (s || '').trim();
    if (!s) return null;
    if (/^[A-Za-z0-9_-]{11}$/.test(s)) return s;
    try {
      const u = new URL(s.includes('://') ? s : 'https://' + s);
      if (u.hostname.includes('youtu.be')) { const id = u.pathname.slice(1, 12); return /^[A-Za-z0-9_-]{11}$/.test(id) ? id : null; }
      const v = u.searchParams.get('v');
      if (v && /^[A-Za-z0-9_-]{11}$/.test(v)) return v;
      const m = u.pathname.match(/\/(shorts|embed|live)\/([A-Za-z0-9_-]{11})/);
      if (m) return m[2];
    } catch (_) { /* not a URL */ }
    const m = s.match(/[A-Za-z0-9_-]{11}/);
    return m ? m[0] : null;
  }
  const thumbUrl = (id, key) => `https://i.ytimg.com/vi/${id}/${key}.jpg`;
  async function downloadThumb(id, key, label) {
    try {
      const res = await fetch(thumbUrl(id, key), { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const blob = await res.blob();
      if (blob.size < 1500) throw new Error('not available');   // YouTube serves a tiny placeholder for a missing size
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${id}_${key}.jpg`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      flash('Saved ' + label);
    } catch (e) { flash(label + ': ' + ((e && e.message) || 'failed')); }
  }
  function renderThumbs(id) {
    const box = $('#thResult');
    if (!box) return;
    if (!id) { box.innerHTML = '<p class="thumb-hint">Paste a video URL or ID, or click “This tab” on a YouTube video.</p>'; return; }
    box.innerHTML = `<a id="thOpen" href="${thumbUrl(id, 'maxresdefault')}" target="_blank" rel="noopener"><img id="thPrev" src="${thumbUrl(id, 'maxresdefault')}" alt="thumbnail"></a>
      <div class="reslist">
        ${THUMB_RES.map(r => `<button type="button" class="btn" data-res="${r.key}" data-label="${r.label}" title="download ${r.dim}">${r.label}</button>`).join('')}
        <button type="button" class="btn" id="thCopy" title="Copy max-res URL">Copy URL</button>
      </div>`;
    const prev = $('#thPrev');
    prev.onerror = () => { prev.onerror = null; prev.src = thumbUrl(id, 'hqdefault'); const o = $('#thOpen'); if (o) o.href = prev.src; };
    $$('#thResult .reslist [data-res]').forEach(b => b.addEventListener('click', () => downloadThumb(id, b.dataset.res, b.dataset.label)));
    $('#thCopy').addEventListener('click', async () => flash(await copyText(thumbUrl(id, 'maxresdefault')) ? 'URL copied' : 'Copy failed'));
  }
  async function useCurrentTab() {
    try {
      const tabs = await api.tabs.query({ active: true, currentWindow: true });
      const id = parseVideoId((tabs && tabs[0] && tabs[0].url) || '');
      if (id) { $('#thInput').value = id; renderThumbs(id); }
      else flash('No YouTube video in this tab');
    } catch (e) { flash('Could not read the tab'); }
  }

  function bind() {
    const on = (sel, ev, fn) => { const el = $(sel); if (el) el.addEventListener(ev, fn); };

    // ---- category router: home directory rows -> sub-pages + a back arrow ----
    const headTitle = $('#headTitle'), back = $('#backBtn'), home = $('#home');
    const TITLES = { thumbnails: 'Thumbnails', youtube: 'YouTube tweaks',
                     studio: 'YouTube Studio', appearance: 'Appearance & size', tester: 'Thumbnail tester' };
    function goHome() {
      home.classList.remove('hidden');
      $$('.page').forEach(p => p.classList.add('hidden'));
      back.classList.add('hidden'); headTitle.textContent = 'CreatorHaven';
    }
    function goPage(id) {
      const p = document.querySelector('.page[data-page="' + id + '"]');
      if (!p) return;
      home.classList.add('hidden');
      $$('.page').forEach(x => x.classList.toggle('hidden', x !== p));
      back.classList.remove('hidden'); headTitle.textContent = TITLES[id] || 'CreatorHaven';
    }
    $$('[data-go]').forEach(t => t.addEventListener('click', () => goPage(t.dataset.go)));
    back.addEventListener('click', goHome);
    goHome();

    document.addEventListener('change', e => {
      const inp = e.target.closest('[data-key]');
      if (inp) save(inp);
    });
    document.addEventListener('input', e => {
      const inp = e.target.closest('[data-key]');
      if (!inp) return;
      if (inp.dataset.key === 'playerWidthPct') $('#pwVal').textContent = inp.value;
      if (inp.dataset.key === 'commentsFontPx') $('#cfVal').textContent = inp.value;
    });
    // connection / probe controls are stripped from this build; guard so nothing crashes
    on('#testBtn', 'click', () => testConnection().catch(e => setStatus('Failed: ' + e.message, 'bad')));
    on('#openSite', 'click', () => api.tabs.create({ url: SF.normalizeSiteUrl($('[data-key="siteUrl"]').value) }));
    on('#probeExport', 'click', async () => {
      const r = await send({ type: 'probe:get' });
      if (!r.ok) { flash('Export failed'); return; }
      const json = JSON.stringify({ exportedAt: new Date().toISOString(), entries: r.log }, null, 2);
      const ok = await copyText(json);
      flash(ok ? 'Copied ' + r.log.length + ' entries' : 'Copy failed');
    });
    on('#probeClear', 'click', async () => { await send({ type: 'probe:clear' }); refreshProbe(); flash('Probe log cleared'); });
    on('#openTester', 'click', () => api.tabs.create({ url: api.runtime.getURL('tester.html') }));
    on('#thInput', 'input', () => renderThumbs(parseVideoId($('#thInput').value)));
    on('#thTab', 'click', useCurrentTab);
    renderThumbs(null);
  }

  bind();
  load().catch(e => { console.warn(TAG, e); setStatus('Could not read settings: ' + e.message, 'bad'); });
})();
