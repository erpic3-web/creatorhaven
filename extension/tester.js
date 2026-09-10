/* CreatorHaven — tester.js. Client-side thumbnail tester: renders the uploaded images inside
 * faithful mock-ups of YouTube's surfaces (home grid, sidebar, search, mobile) in light and
 * dark, A/B side by side, and shuffled among grey competitor cards. No network, no upload. */
(() => {
  'use strict';

  const $ = s => document.querySelector(s);
  const MAX_THUMBS = 4;
  const LABELS = ['A', 'B', 'C', 'D'];
  const COMPETITORS = [
    'I Spent 100 Hours Learning This So You Don’t Have To',
    'We Finally Tested It (results surprised us)',
    'The Truth About This Everyone Ignores',
    'Building a Tiny House in 7 Days — Full Timelapse',
    'Ranking Every Version From Worst to Best',
    'Why Nobody Talks About This Anymore',
    '24 Hours With Zero Budget Challenge',
    'Beginner Mistakes I Still See Every Day',
    'Reacting to Your Setups (part 12)',
    'This Changed How I Work Forever'
  ];
  const COMP_CHANNELS = ['Nova Labs', 'Daily Byte', 'Marcus V.', 'Studio Ten', 'Kelsey Makes', 'The Loop', 'Pixel Path', 'Orbit', 'Greenroom', 'Tinker'];
  const COMP_META = ['842K views • 2 days ago', '1.1M views • 5 days ago', '312K views • 1 week ago', '2.4M views • 3 weeks ago', '98K views • 12 hours ago', '4.7M views • 1 month ago', '567K views • 4 days ago', '1.9M views • 2 weeks ago'];

  const state = {
    thumbs: [],           // [{ name, url }]
    seed: 1,
    title: '', channel: '', views: '', age: '', duration: '', theme: 'both'
  };

  /* ---------------------------------------------------------------- helpers */

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // Deterministic PRNG so "Shuffle" is the only thing that reorders competitors.
  function rng(seed) {
    let s = seed >>> 0 || 1;
    return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  }

  function hashHue(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
    return h % 360;
  }

  function avatar(name, ph) {
    const a = el('div', 'avatar' + (ph ? ' ph' : ''));
    if (!ph) {
      a.textContent = (name || '?').trim().charAt(0).toUpperCase() || '?';
      a.style.background = 'hsl(' + hashHue(name || 'x') + ' 55% 45%)';
    }
    return a;
  }

  function thumbBox(thumb, opts) {
    const box = el('div', 'thumb' + (thumb ? '' : ' ph'));
    if (thumb) {
      const img = el('img');
      img.src = thumb.url; img.alt = thumb.name;
      box.appendChild(img);
    }
    if (opts && opts.label) box.appendChild(el('span', 'ab', opts.label));
    if (state.duration && (thumb || (opts && opts.fakeDuration))) box.appendChild(el('span', 'dur', thumb ? state.duration : opts.fakeDuration));
    return box;
  }

  function metaLines(channel, meta) {
    const m = el('div', 'meta');
    m.appendChild(el('div', null, channel));
    m.appendChild(el('div', null, meta));
    return m;
  }

  function myMeta() { return [state.views, state.age].filter(Boolean).join(' • '); }

  /* ---------------------------------------------------------------- cards */

  function homeCard(thumb, label, comp) {
    const card = el('div', 'card');
    card.appendChild(thumbBox(thumb, thumb ? { label } : { fakeDuration: comp.dur }));
    const body = el('div', 'body');
    body.appendChild(avatar(thumb ? state.channel : comp.channel, !thumb));
    const text = el('div', 'text');
    text.appendChild(el('div', 'title', thumb ? state.title : comp.title));
    text.appendChild(metaLines(thumb ? state.channel : comp.channel, thumb ? myMeta() : comp.meta));
    body.appendChild(text);
    card.appendChild(body);
    return card;
  }

  function sideRow(thumb, label, comp) {
    const row = el('div', 'side');
    row.appendChild(thumbBox(thumb, thumb ? { label } : { fakeDuration: comp.dur }));
    const text = el('div', 'text');
    text.appendChild(el('div', 'title', thumb ? state.title : comp.title));
    text.appendChild(metaLines(thumb ? state.channel : comp.channel, thumb ? myMeta() : comp.meta));
    row.appendChild(text);
    return row;
  }

  function searchRow(thumb, label) {
    const row = el('div', 'search');
    row.appendChild(thumbBox(thumb, { label }));
    const text = el('div', 'text');
    text.appendChild(el('div', 'title', state.title));
    text.appendChild(metaLines(myMeta(), ''));
    const chan = el('div', 'chan');
    chan.appendChild(avatar(state.channel));
    chan.appendChild(el('span', null, state.channel));
    text.appendChild(chan);
    text.appendChild(el('div', 'snippet', 'In this video: ' + state.title + '. Watch till the end for the result — timestamps in the description.'));
    row.appendChild(text);
    return row;
  }

  function mobileCard(thumb, label) {
    const card = el('div', 'mob');
    card.appendChild(thumbBox(thumb, { label }));
    const body = el('div', 'body');
    body.appendChild(avatar(state.channel));
    const text = el('div', 'text');
    text.appendChild(el('div', 'title', state.title));
    text.appendChild(el('div', 'meta', [state.channel, myMeta()].filter(Boolean).join(' • ')));
    body.appendChild(text);
    card.appendChild(body);
    return card;
  }

  function competitor(i) {
    return {
      title: COMPETITORS[i % COMPETITORS.length],
      channel: COMP_CHANNELS[i % COMP_CHANNELS.length],
      meta: COMP_META[i % COMP_META.length],
      dur: (8 + (i * 7) % 20) + ':' + String((i * 13) % 60).padStart(2, '0')
    };
  }

  /* ---------------------------------------------------------------- sections */

  function themes() {
    if (state.theme === 'light') return ['light'];
    if (state.theme === 'dark') return ['dark'];
    return ['light', 'dark'];
  }

  function section(title, build) {
    const sec = el('section', 'sec');
    sec.appendChild(el('h2', null, title));
    const wrap = el('div', 'themes');
    for (const th of themes()) {
      const panel = el('div', 'yt yt-' + th);
      panel.appendChild(el('div', 'theme-tag', th + ' theme'));
      build(panel);
      wrap.appendChild(panel);
    }
    sec.appendChild(wrap);
    return sec;
  }

  function render() {
    const host = $('#previews');
    host.textContent = '';
    if (!state.thumbs.length) {
      host.appendChild(el('p', 'empty muted', 'Add a thumbnail to see it in the home grid, the sidebar, search results and on a phone.'));
      return;
    }
    const mine = state.thumbs.map((t, i) => ({ thumb: t, label: state.thumbs.length > 1 ? LABELS[i] : '' }));

    host.appendChild(section(mine.length > 1 ? 'Home grid — A/B side by side (desktop, 360 px cards)' : 'Home grid (desktop, 360 px card)', panel => {
      const grid = el('div', 'home-grid');
      for (const m of mine) grid.appendChild(homeCard(m.thumb, m.label));
      panel.appendChild(grid);
    }));

    host.appendChild(section('Sidebar “Up next” (168 px thumbnails)', panel => {
      const col = el('div', 'side-col');
      let ci = 0;
      col.appendChild(sideRow(null, '', competitor(ci++)));
      for (const m of mine) { col.appendChild(sideRow(m.thumb, m.label)); col.appendChild(sideRow(null, '', competitor(ci++))); }
      panel.appendChild(col);
    }));

    host.appendChild(section('Search result (360 px thumbnail)', panel => {
      for (const m of mine) panel.appendChild(searchRow(m.thumb, m.label));
    }));

    host.appendChild(section('Mobile feed (412 px phone)', panel => {
      const phone = el('div', 'phone');
      const bar = el('div', 'bar');
      bar.appendChild(el('span', 'play'));
      bar.appendChild(document.createTextNode('YouTube'));
      phone.appendChild(bar);
      for (const m of mine) phone.appendChild(mobileCard(m.thumb, m.label));
      panel.appendChild(phone);
    }));

    host.appendChild(section('Among competitors — 8 grey cards + yours (shuffle to reorder)', panel => {
      const grid = el('div', 'home-grid compact');
      const rand = rng(state.seed);
      const slots = [];
      for (let i = 0; i < 8; i++) slots.push({ comp: competitor(i) });
      for (const m of mine) slots.splice(Math.floor(rand() * (slots.length + 1)), 0, m);
      for (const s of slots) grid.appendChild(s.thumb ? homeCard(s.thumb, s.label) : homeCard(null, '', s.comp));
      panel.appendChild(grid);
    }));
  }

  /* ---------------------------------------------------------------- inputs */

  function readInputs() {
    state.title = $('#title').value.trim() || 'Untitled video';
    state.channel = $('#channel').value.trim() || 'Your Channel';
    state.views = $('#views').value.trim();
    state.age = $('#age').value.trim();
    state.duration = $('#duration').value.trim();
    state.theme = $('#theme').value;
  }

  function renderThumbList() {
    const list = $('#thumbList');
    list.textContent = '';
    state.thumbs.forEach((t, i) => {
      const li = el('li');
      li.appendChild(el('span', 'tag', LABELS[i]));
      const img = el('img'); img.src = t.url; img.alt = '';
      li.appendChild(img);
      li.appendChild(el('span', 'name', t.name));
      const rm = el('button', null, '✕');
      rm.type = 'button'; rm.title = 'Remove';
      rm.addEventListener('click', () => { URL.revokeObjectURL(t.url); state.thumbs.splice(i, 1); renderThumbList(); render(); });
      li.appendChild(rm);
      list.appendChild(li);
    });
  }

  function addFiles(files) {
    const images = Array.from(files || []).filter(f => f && /^image\//.test(f.type));
    if (!images.length) return;
    const room = MAX_THUMBS - state.thumbs.length;
    for (const f of images.slice(0, Math.max(0, room))) state.thumbs.push({ name: f.name, url: URL.createObjectURL(f) });
    if (images.length > room) alert('Up to ' + MAX_THUMBS + ' thumbnails at a time — the first ' + Math.max(0, room) + ' were added.');
    renderThumbList();
    render();
  }

  function bind() {
    const drop = $('#drop');
    $('#file').addEventListener('change', e => { addFiles(e.target.files); e.target.value = ''; });
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); addFiles(e.dataTransfer && e.dataTransfer.files); });
    document.addEventListener('dragover', e => e.preventDefault());
    document.addEventListener('drop', e => { if (!drop.contains(e.target)) { e.preventDefault(); addFiles(e.dataTransfer && e.dataTransfer.files); } });
    document.addEventListener('paste', e => { const items = e.clipboardData && e.clipboardData.files; if (items && items.length) addFiles(items); });

    const rerender = () => { readInputs(); render(); };
    for (const id of ['title', 'channel', 'views', 'age', 'duration']) $('#' + id).addEventListener('input', rerender);
    $('#theme').addEventListener('change', rerender);
    $('#shuffle').addEventListener('click', () => { state.seed = (state.seed * 7 + 13) % 2147483647; render(); });
    $('#clear').addEventListener('click', () => { for (const t of state.thumbs) URL.revokeObjectURL(t.url); state.thumbs = []; renderThumbList(); render(); });
  }

  bind();
  readInputs();
  render();
})();
