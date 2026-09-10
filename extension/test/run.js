/* CreatorHaven — test/run.js
 * Node unit tests for lib/common.js plus static checks over the whole extension.
 * No dependencies. Run:  node test/run.js
 * Prints "ALL PASS n/n" (exit 0) or the failures (exit 1).
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const { spawnSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const SF = require(path.join(ROOT, 'lib', 'common.js'));

const tests = [];
function t(name, fn) { tests.push({ name, fn }); }
const read = rel => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const exists = rel => fs.existsSync(path.join(ROOT, rel));

/* ================================================================ parseVideoId */
const ID = 'dQw4w9WgXcQ';
t('parseVideoId: watch?v=', () => assert.equal(SF.parseVideoId('https://www.youtube.com/watch?v=' + ID), ID));
t('parseVideoId: watch with extra params, v not first', () => assert.equal(SF.parseVideoId('https://www.youtube.com/watch?feature=share&v=' + ID + '&t=42s&list=PLx'), ID));
t('parseVideoId: youtu.be/', () => assert.equal(SF.parseVideoId('https://youtu.be/' + ID + '?si=abc123'), ID));
t('parseVideoId: /shorts/', () => assert.equal(SF.parseVideoId('https://www.youtube.com/shorts/' + ID), ID));
t('parseVideoId: /embed/ with params', () => assert.equal(SF.parseVideoId('https://www.youtube.com/embed/' + ID + '?autoplay=1&rel=0'), ID));
t('parseVideoId: /live/', () => assert.equal(SF.parseVideoId('https://www.youtube.com/live/' + ID + '?feature=share'), ID));
t('parseVideoId: relative /watch?v=', () => assert.equal(SF.parseVideoId('/watch?v=' + ID), ID));
t('parseVideoId: m.youtube.com and youtube-nocookie', () => {
  assert.equal(SF.parseVideoId('https://m.youtube.com/watch?v=' + ID), ID);
  assert.equal(SF.parseVideoId('https://www.youtube-nocookie.com/embed/' + ID), ID);
});
t('parseVideoId: rejects non-video URLs and junk', () => {
  assert.equal(SF.parseVideoId('https://www.youtube.com/playlist?list=PL123'), null);
  assert.equal(SF.parseVideoId('https://example.com/watch?v=' + ID), null);
  assert.equal(SF.parseVideoId('https://www.youtube.com/watch?v=short'), null);
  assert.equal(SF.parseVideoId('https://www.youtube.com/@handle'), null);
  assert.equal(SF.parseVideoId(''), null);
  assert.equal(SF.parseVideoId(null), null);
});

/* ================================================================ parseChannelRef */
const UC = 'UCX6OQ3DkcsbYNE6H8uQQuVA';
t('parseChannelRef: /@handle URL', () => assert.deepEqual(SF.parseChannelRef('https://www.youtube.com/@MrBeast'), { type: 'handle', handle: '@MrBeast' }));
t('parseChannelRef: /@handle/videos', () => assert.deepEqual(SF.parseChannelRef('https://www.youtube.com/@Some.Name_1/videos?x=1'), { type: 'handle', handle: '@Some.Name_1' }));
t('parseChannelRef: /channel/UC…', () => assert.deepEqual(SF.parseChannelRef('https://www.youtube.com/channel/' + UC + '/about'), { type: 'id', id: UC }));
t('parseChannelRef: /c/name and /user/name', () => {
  assert.deepEqual(SF.parseChannelRef('https://www.youtube.com/c/SomeName'), { type: 'custom', name: 'SomeName' });
  assert.deepEqual(SF.parseChannelRef('/user/legacyUser'), { type: 'user', name: 'legacyUser' });
});
t('parseChannelRef: bare @handle and bare UC id', () => {
  assert.deepEqual(SF.parseChannelRef('@handle'), { type: 'handle', handle: '@handle' });
  assert.deepEqual(SF.parseChannelRef(UC), { type: 'id', id: UC });
});
t('parseChannelRef: rejects foreign hosts, short handles, bad ids', () => {
  assert.equal(SF.parseChannelRef('https://example.com/@x'), null);
  assert.equal(SF.parseChannelRef('@ab'), null);
  assert.equal(SF.parseChannelRef('https://www.youtube.com/channel/UCshort'), null);
  assert.equal(SF.parseChannelRef('https://www.youtube.com/watch?v=' + ID), null);
});

/* ================================================================ handles */
t('normalizeHandle / handleKey', () => {
  assert.equal(SF.normalizeHandle('someone'), '@someone');
  assert.equal(SF.normalizeHandle(' @Some.One. '), '@Some.One');
  assert.equal(SF.normalizeHandle('@ab'), '');
  assert.equal(SF.handleKey('@Some.One'), 'hn:@some.one');
  assert.equal(SF.handleKey('bad!'), '');
});
t('extractHandles: unique, ordered, trailing dot stripped, emails ignored', () => {
  const out = SF.extractHandles(['hello @Some.One. and @two_2', '@some.one again', 'mail me at x@nothandle.com', '@ab short']);
  assert.deepEqual(out, ['@Some.One', '@two_2']);
  assert.deepEqual(SF.extractHandles('just @solo'), ['@solo']);
  assert.deepEqual(SF.extractHandles([]), []);
});

/* ================================================================ dates */
t('relativeDateRegex: matches relative phrases', () => {
  for (const s of ['3 weeks ago', 'Streamed 2 days ago', 'Premiered 1 hour ago', '1 year ago', '3,215 views 12 minutes ago']) {
    assert.ok(SF.relativeDateRegex.test(s), s);
  }
});
t('relativeDateRegex: rejects fragments', () => {
  for (const s of ['3 weeks', 'ago', 'Sep 8, 2026', '3 weeks agoish', 'weeks ago']) {
    assert.ok(!SF.relativeDateRegex.test(s), s);
  }
});
t('formatExactDate: contains the year (local time, en-US short month)', () => {
  const s = SF.formatExactDate('2026-09-08T15:41:32-07:00');
  assert.ok(s.includes('2026'), s);
  assert.ok(/Sep/.test(s), s);
  assert.ok(/\d{1,2}:\d{2}/.test(s), s);
  assert.equal(SF.formatExactDate('not a date'), '');
});
t('extractPublishDate: finds "publishDate" in script text', () => {
  assert.equal(SF.extractPublishDate('{"microformat":{"playerMicroformatRenderer":{"publishDate":"2026-09-08T15:41:32-07:00","x":1}}}'), '2026-09-08T15:41:32-07:00');
  assert.equal(SF.extractPublishDate('nothing here'), null);
});

/* ================================================================ numbers */
t('compactNumber: 1234 -> 1.2K, 1250000 -> 1.3M', () => {
  assert.equal(SF.compactNumber(1234), '1.2K');
  assert.equal(SF.compactNumber(1250000), '1.3M');
});
t('compactNumber: strips .0, handles edges', () => {
  assert.equal(SF.compactNumber(1000), '1K');
  assert.equal(SF.compactNumber(999), '999');
  assert.equal(SF.compactNumber(999950), '1M');
  assert.equal(SF.compactNumber(2500000000), '2.5B');
  assert.equal(SF.compactNumber(-1500), '-1.5K');
  assert.equal(SF.compactNumber('12000'), '12K');
  assert.equal(SF.compactNumber('x'), '');
});
t('numberBlurRegex: 1,234 / 12.5K / 12 hours match; 2 hours does not', () => {
  assert.equal(('1,234'.match(SF.numberBlurRegex) || [])[0], '1,234');
  assert.equal(('12.5K'.match(SF.numberBlurRegex) || [])[0], '12.5K');
  assert.equal(('12 hours'.match(SF.numberBlurRegex) || [])[0], '12');
  assert.equal('2 hours'.match(SF.numberBlurRegex), null);
  assert.equal('ago'.match(SF.numberBlurRegex), null);
  assert.equal('5K'.match(SF.numberBlurRegex), null);
});
t('segmentNumbers: round-trips and flags only the numbers', () => {
  const text = 'Views 1,234 in 12 hours, 2 likes, 3.5K subs';
  const segs = SF.segmentNumbers(text);
  assert.equal(SF.joinSegments(segs), text);
  assert.deepEqual(segs.filter(s => s.blur).map(s => s.text), ['1,234', '12', '3.5K']);
  assert.deepEqual(SF.segmentNumbers(''), []);
  assert.ok(SF.hasNumberRun('12 hours'));
  assert.ok(!SF.hasNumberRun('2 hours'));
});

/* ================================================================ thumbnails */
t('pickThumbUrl: sizes and shorthands', () => {
  assert.equal(SF.pickThumbUrl(ID, 'maxres'), 'https://i.ytimg.com/vi/' + ID + '/maxresdefault.jpg');
  assert.equal(SF.pickThumbUrl(ID, 'sd'), 'https://i.ytimg.com/vi/' + ID + '/sddefault.jpg');
  assert.equal(SF.pickThumbUrl(ID, 'hq'), 'https://i.ytimg.com/vi/' + ID + '/hqdefault.jpg');
  assert.equal(SF.pickThumbUrl(ID, 'mqdefault'), 'https://i.ytimg.com/vi/' + ID + '/mqdefault.jpg');
  assert.equal(SF.pickThumbUrl(ID, 'default'), 'https://i.ytimg.com/vi/' + ID + '/default.jpg');
  assert.equal(SF.pickThumbUrl(ID), 'https://i.ytimg.com/vi/' + ID + '/maxresdefault.jpg');
  assert.equal(SF.pickThumbUrl(ID, 'bogus'), 'https://i.ytimg.com/vi/' + ID + '/maxresdefault.jpg');
  assert.deepEqual(SF.THUMB_SIZES.slice(), ['maxresdefault', 'sddefault', 'hqdefault', 'mqdefault', 'default']);
});
t('isPlaceholderThumb: < 3000 bytes is the 120x90 placeholder', () => {
  assert.ok(SF.isPlaceholderThumb(1097));
  assert.ok(SF.isPlaceholderThumb(undefined));
  assert.ok(!SF.isPlaceholderThumb(48000));
});

/* ================================================================ probe */
t('probeHitKeyRegex: matches the interesting keys only', () => {
  for (const k of ['realtimeViews', 'last48Hours', 'last60Minutes', 'fortyEightHourData', 'sixtyMinuteData', 'engagedViews', 'monetizationStatus', 'adBreaks', 'midrollEnabled', 'isMonetized']) {
    assert.ok(SF.probeHitKeyRegex.test(k), k);
  }
  for (const k of ['viewCount', 'title', 'videoId', 'lastWeek', 'ads']) assert.ok(!SF.probeHitKeyRegex.test(k), k);
});
t('collectProbeHits: paths, values, short strings only', () => {
  const doc = { a: { realtimeViews: 42, list: [{ engagedViews: '7' }, { x: 1 }], monetizationStatus: 'x'.repeat(41) }, adBreaks: [1, 2] };
  const hits = SF.collectProbeHits(doc);
  const byKey = Object.fromEntries(hits.map(h => [h.path, h]));
  assert.equal(byKey['$.a.realtimeViews'].value, 42);
  assert.equal(byKey['$.a.list[0].engagedViews'].value, '7');
  assert.equal(byKey['$.a.monetizationStatus'].value, undefined);
  assert.equal(byKey['$.a.monetizationStatus'].type, 'string');
  assert.equal(byKey['$.adBreaks'].type, 'array');
  assert.equal(hits.length, 4);
});
t('collectProbeHits: honours depth, node and hit limits', () => {
  let deep = { realtimeViews: 1 };
  for (let i = 0; i < 12; i++) deep = { n: deep };
  assert.equal(SF.collectProbeHits(deep).length, 0);            // buried beyond depth 8
  assert.equal(SF.collectProbeHits(deep, { maxDepth: 20 }).length, 1);
  const wide = { items: Array.from({ length: 6000 }, () => ({ engagedViews: 1 })) };
  assert.ok(SF.collectProbeHits(wide).length < 6000);
  assert.equal(SF.collectProbeHits(wide, { maxNodes: 100000, maxHits: 10 }).length, 10);
  assert.deepEqual(SF.collectProbeHits(null), []);
  assert.deepEqual(SF.collectProbeHits('str'), []);
});
t('mergeProbeEntry: dedupes by url+status, counts, keeps first keys, unions hits', () => {
  let log = [];
  log = SF.mergeProbeEntry(log, { url: '/youtubei/v1/a', status: 200, keys: ['x', 'y'], size: 10, ts: 1, hits: [{ path: '$.x', key: 'x' }] });
  log = SF.mergeProbeEntry(log, { url: '/youtubei/v1/a', status: 200, keys: ['z'], size: 20, ts: 2, hits: [{ path: '$.x', key: 'x' }, { path: '$.y', key: 'y' }] });
  log = SF.mergeProbeEntry(log, { url: '/youtubei/v1/a', status: 500, keys: [], size: 0, ts: 3 });
  assert.equal(log.length, 2);
  assert.equal(log[0].count, 2);
  assert.deepEqual(log[0].keys, ['x', 'y']);
  assert.equal(log[0].size, 10);
  assert.equal(log[0].lastSize, 20);
  assert.equal(log[0].firstSeen, 1);
  assert.equal(log[0].lastSeen, 2);
  assert.equal(log[0].hits.length, 2);
  assert.equal(log[1].status, 500);
});
t('mergeProbeEntry: caps at 300, dropping the oldest first-seen', () => {
  let log = [];
  for (let i = 0; i < 305; i++) log = SF.mergeProbeEntry(log, { url: '/u/' + i, status: 200, ts: i });
  assert.equal(log.length, 300);
  assert.equal(log[0].url, '/u/5');
  assert.equal(log[299].url, '/u/304');
  assert.equal(SF.PROBE_LOG_CAP, 300);
});
t('channelIdFromPath / pathOnly', () => {
  assert.equal(SF.channelIdFromPath('/channel/' + UC + '/analytics/tab-overview/period-default'), UC);
  assert.equal(SF.channelIdFromPath('/channel/' + UC), UC);
  assert.equal(SF.channelIdFromPath('/videos/upload'), null);
  assert.equal(SF.pathOnly('https://studio.youtube.com/youtubei/v1/creator/get_creator_channels?alt=json&key=x#f'), '/youtubei/v1/creator/get_creator_channels');
  assert.equal(SF.pathOnly('/youtubei/v1/x?y=1'), '/youtubei/v1/x');
});

/* ================================================================ settings + generic */
t('SF_DEFAULTS: every documented key with its default', () => {
  const d = SF.SF_DEFAULTS;
  assert.equal(d.siteUrl, 'http://127.0.0.1:5800');
  assert.equal(d.token, ''); assert.equal(d.discordUrl, '');
  for (const k of ['confirmSignOut', 'exactDates', 'realNames', 'subCounts', 'feedCleaner', 'thumbDownloader', 'playlistSearch', 'videoTags', 'favorites', 'studioProbe', 'studioDiscordButton']) assert.equal(d[k], true, k);
  for (const k of ['shortsRedirect', 'dontRecommend', 'appearance', 'appearanceCompact', 'sizeCustomizer', 'streamerMode']) assert.equal(d[k], false, k);
  assert.equal(d.appearanceAccent, '#ff0000'); assert.equal(d.appearanceFont, '');
  assert.equal(d.playerWidthPct, 100); assert.equal(d.commentsFontPx, 14);
  assert.ok(Object.isFrozen(d));
});
t('withDefaults: coerces types, drops unknown keys, ignores null', () => {
  const s = SF.withDefaults({ playerWidthPct: '120', exactDates: 0, shortsRedirect: 'true', token: 123, bogus: 1, siteUrl: null, commentsFontPx: 'abc' });
  assert.equal(s.playerWidthPct, 120);
  assert.equal(s.exactDates, false);
  assert.equal(s.shortsRedirect, true);
  assert.equal(s.token, '123');
  assert.equal(s.siteUrl, 'http://127.0.0.1:5800');
  assert.equal(s.commentsFontPx, 14);
  assert.ok(!('bogus' in s));
  assert.equal(SF.withDefaults(undefined).realNames, true);
});
t('normalizeSiteUrl', () => {
  assert.equal(SF.normalizeSiteUrl('127.0.0.1:5800/'), 'http://127.0.0.1:5800');
  assert.equal(SF.normalizeSiteUrl('https://forge.example.com///'), 'https://forge.example.com');
  assert.equal(SF.normalizeSiteUrl(''), 'http://127.0.0.1:5800');
});
t('clamp / chunk / uniq / safeJsonParse', () => {
  assert.equal(SF.clamp(250, 50, 200), 200);
  assert.equal(SF.clamp('x', 50, 200), 50);
  assert.equal(SF.clamp(75, 50, 200), 75);
  assert.deepEqual(SF.chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
  assert.deepEqual(SF.chunk([], 3), []);
  assert.deepEqual(SF.uniq(['a', 'b', 'a']), ['a', 'b']);
  assert.deepEqual(SF.safeJsonParse('{"a":1}'), { a: 1 });
  assert.equal(SF.safeJsonParse('{'), undefined);
  assert.equal(SF.MAX_HANDLES_PER_REQUEST, 50);
});
t('shelfBlockRegex: Shorts / Playables / posts / news headers', () => {
  for (const s of ['Shorts', 'Playables', 'Latest YouTube posts', 'Breaking news', 'YouTube Playables']) assert.ok(SF.shelfBlockRegex.test(s), s);
  for (const s of ['Recommended', 'Trending', 'Short films', 'News']) assert.ok(!SF.shelfBlockRegex.test(s), s);
});
t('debounce: trailing edge, single call, cancel', async () => {
  let calls = 0;
  const fn = SF.debounce(() => { calls++; }, 20);
  fn(); fn(); fn();
  assert.equal(calls, 0);
  await new Promise(r => setTimeout(r, 60));
  assert.equal(calls, 1);
  fn(); fn.cancel();
  await new Promise(r => setTimeout(r, 40));
  assert.equal(calls, 1);
  fn(); fn.flush();
  assert.equal(calls, 2);
});

/* ================================================================ static checks */
function listJs(dir, out) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    if (name === 'node_modules' || name.startsWith('.')) continue;
    if (fs.statSync(p).isDirectory()) listJs(p, out);
    else if (name.endsWith('.js')) out.push(p);
  }
  return out;
}
const JS_FILES = listJs(ROOT, []).map(p => path.relative(ROOT, p).split(path.sep).join('/')).sort();
const CLASSIC_SCRIPTS = ['lib/common.js', 'bg.js', 'content/yt.js', 'content/studio.js', 'content/studio_probe_main.js', 'popup.js', 'tester.js'];

let manifest = null;
t('manifest.json: parses, MV3, required fields', () => {
  manifest = JSON.parse(read('manifest.json'));
  assert.equal(manifest.manifest_version, 3);
  assert.ok(manifest.name && manifest.version && manifest.description);
  assert.equal(manifest.action.default_popup, 'popup.html');
  assert.equal(manifest.background.service_worker, 'bg.js');
  assert.deepEqual(manifest.background.scripts, ['lib/common.js', 'bg.js']);
  assert.ok(manifest.permissions.includes('storage') && manifest.permissions.includes('alarms'));
  assert.ok(!manifest.permissions.includes('downloads'), 'no downloads permission by design');
  assert.ok(manifest.host_permissions.includes('https://i.ytimg.com/*'));
  assert.ok(manifest.host_permissions.includes('http://127.0.0.1:5800/*'));
  assert.equal(manifest.content_scripts.length, 3);
});
t('manifest.json: every referenced file exists', () => {
  const refs = [];
  for (const v of Object.values(manifest.icons)) refs.push(v);
  for (const v of Object.values(manifest.action.default_icon)) refs.push(v);
  refs.push(manifest.action.default_popup, manifest.background.service_worker, ...manifest.background.scripts);
  for (const cs of manifest.content_scripts) refs.push(...(cs.js || []), ...(cs.css || []));
  for (const r of refs) assert.ok(exists(r), 'missing ' + r);
});
t('manifest.json: exactly one MAIN-world entry, self-contained, on studio only', () => {
  const main = manifest.content_scripts.filter(cs => cs.world === 'MAIN');
  assert.equal(main.length, 1);
  assert.deepEqual(main[0].js, ['content/studio_probe_main.js']);
  assert.deepEqual(main[0].matches, ['https://studio.youtube.com/*']);
  assert.equal(main[0].run_at, 'document_start');
  const iso = manifest.content_scripts.filter(cs => cs.world !== 'MAIN');
  for (const cs of iso) assert.equal(cs.js[0], 'lib/common.js', 'common.js must load before ' + cs.js[1]);
});
t('node --check passes for every .js file (' + JS_FILES.length + ' files)', () => {
  assert.ok(JS_FILES.length >= 9, JS_FILES.join(','));
  for (const f of JS_FILES) {
    const r = spawnSync(process.execPath, ['--check', path.join(ROOT, f)], { encoding: 'utf8' });
    assert.equal(r.status, 0, f + ': ' + (r.stderr || r.stdout));
  }
});
t('classic scripts contain no import/export statements', () => {
  for (const f of CLASSIC_SCRIPTS) {
    const src = read(f).split('\n').filter(l => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
    assert.ok(!/^\s*(import|export)\b/m.test(src), f + ' has an import/export statement');
    assert.ok(!/\brequire\s*\(/.test(src), f + ' uses require()');
  }
});
t('content scripts are wrapped in an IIFE and use the browser/chrome shim', () => {
  for (const f of ['content/yt.js', 'content/studio.js', 'content/studio_probe_main.js', 'popup.js', 'tester.js']) {
    const src = read(f);
    const firstCode = src.split('\n').find(l => l.trim() && !/^\s*(\/\/|\*|\/\*)/.test(l)) || '';
    assert.ok(/^\s*\((\(\)\s*=>|function)/.test(firstCode), f + ' should start with an IIFE, got: ' + firstCode);
    assert.ok(/\)\(\);\s*$/.test(src.trim()), f + ' should end by invoking the IIFE');
  }
  for (const f of ['content/yt.js', 'content/studio.js', 'popup.js', 'bg.js']) {
    assert.ok(read(f).includes('globalThis.browser ?? globalThis.chrome'), f + ' missing api shim');
  }
});
t('probe regex in studio_probe_main.js matches SF.probeHitKeyRegex', () => {
  const src = read('content/studio_probe_main.js');
  const m = src.match(/HIT_RE\s*=\s*\/(.+?)\/([a-z]*);/);
  assert.ok(m, 'HIT_RE not found');
  assert.equal(m[1], SF.probeHitKeyRegex.source);
  assert.equal(m[2], SF.probeHitKeyRegex.flags);
  assert.ok(src.includes('.clone()'), 'fetch responses must be read from a clone');
  assert.ok(src.includes('/youtubei/v1/'));
  assert.ok(!src.includes('globalThis.SF'), 'MAIN-world script must not depend on lib/common.js');
});
t('bg.js: every message type the content scripts / popup send has a handler', () => {
  const bg = read('bg.js');
  const senders = read('content/yt.js') + read('content/studio.js') + read('popup.js');
  const types = new Set();
  for (const m of senders.matchAll(/type:\s*'([a-zA-Z:]+)'/g)) types.add(m[1]);
  assert.ok(types.size >= 6, Array.from(types).join(','));
  for (const ty of types) assert.ok(new RegExp('(^|[\\s{,])\'?' + ty.replace(':', '\\:') + '\'?\\s*[:,}]').test(bg) || bg.includes("'" + ty + "'") || bg.includes('  ' + ty + ','), 'bg.js has no handler for ' + ty);
  assert.ok(bg.includes('AbortController') && bg.includes('FETCH_TIMEOUT_MS = 10000'), '10 s fetch timeout');
});
t('popup.html / tester.html: no inline scripts or handlers (MV3 CSP), assets exist', () => {
  for (const f of ['popup.html', 'tester.html']) {
    const html = read(f);
    assert.ok(!/<script(?![^>]*\bsrc=)/i.test(html), f + ' has an inline <script>');
    assert.ok(!/\son[a-z]+\s*=/i.test(html), f + ' has an inline event handler');
    for (const m of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
      const ref = m[1];
      if (/^(https?:|#|mailto:)/.test(ref)) continue;
      assert.ok(exists(ref), f + ' references missing ' + ref);
    }
    assert.ok(html.includes('lib/common.js'), f + ' must load lib/common.js first');
  }
  const popup = read('popup.html');
  for (const k of Object.keys(SF.SF_DEFAULTS)) assert.ok(popup.includes('data-key="' + k + '"'), 'popup lacks a control for ' + k);
});
t('css files only use sf- prefixed hooks of our own', () => {
  for (const f of ['content/yt.css', 'content/studio.css']) {
    const css = read(f).replace(/\/\*[\s\S]*?\*\//g, '');   // comments mention "youtube.com"
    for (const m of css.matchAll(/\.([a-zA-Z][\w-]*)/g)) {
      const cls = m[1];
      assert.ok(cls.startsWith('sf-') || /^(ytd|ytp|yt|tp-yt)-/.test(cls), f + ': class .' + cls + ' is not sf- prefixed');
    }
  }
});

function crc32(buf) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) {
    let x = (c ^ buf[i]) & 0xFF;
    for (let k = 0; k < 8; k++) x = (x & 1) ? (0xEDB88320 ^ (x >>> 1)) : (x >>> 1);
    c = x ^ (c >>> 8);
  }
  return (c ^ 0xFFFFFFFF) >>> 0;
}
t('icons: valid PNGs at 16/48/128 (signature, IHDR, CRC, inflatable IDAT, IEND)', () => {
  for (const size of [16, 48, 128]) {
    const buf = fs.readFileSync(path.join(ROOT, 'icons', 'icon' + size + '.png'));
    assert.deepEqual(Array.from(buf.subarray(0, 8)), [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A], 'png signature ' + size);
    let off = 8;
    const chunks = [];
    while (off < buf.length) {
      const len = buf.readUInt32BE(off);
      const type = buf.toString('ascii', off + 4, off + 8);
      const data = buf.subarray(off + 8, off + 8 + len);
      const crc = buf.readUInt32BE(off + 8 + len);
      assert.equal(crc, crc32(buf.subarray(off + 4, off + 8 + len)), 'crc ' + type + ' ' + size);
      chunks.push({ type, data });
      off += 12 + len;
    }
    assert.equal(chunks[0].type, 'IHDR');
    assert.equal(chunks[0].data.readUInt32BE(0), size);
    assert.equal(chunks[0].data.readUInt32BE(4), size);
    assert.equal(chunks[0].data[8], 8); assert.equal(chunks[0].data[9], 6);
    assert.equal(chunks[chunks.length - 1].type, 'IEND');
    const idat = Buffer.concat(chunks.filter(c => c.type === 'IDAT').map(c => c.data));
    const raw = zlib.inflateSync(idat);
    assert.equal(raw.length, (size * 4 + 1) * size, 'raw length ' + size);
    let opaque = 0, teal = 0;
    const stride = size * 4 + 1;
    for (let y = 0; y < size; y++) {
      assert.equal(raw[y * stride], 0, 'filter byte row ' + y);
      for (let x = 0; x < size; x++) {
        const o = y * stride + 1 + x * 4;
        if (raw[o + 3] >= 128) opaque++;   // anti-aliased edges at 16 px are semi-transparent
        if (raw[o] < 200 && raw[o + 1] > 150 && raw[o + 2] > 150 && raw[o + 3] === 255) teal++;   // the CreatorHaven roof
      }
    }
    assert.ok(opaque > size * size * 0.3, 'icon ' + size + ' looks empty');   // the house mark on a transparent square
    assert.ok(teal > 10, 'icon ' + size + ' has no teal roof pixels');
  }
});
t('docs: LOADING.md exists and covers Chrome + Firefox', () => {
  const doc = read('LOADING.md');
  assert.ok(/chrome:\/\/extensions/.test(doc));
  assert.ok(/about:debugging/.test(doc));
});

/* ================================================================ thumbnail grabber */
t('thumbGrabber: default true in SF_DEFAULTS and popup has the toggle', () => {
  assert.equal(SF.SF_DEFAULTS.thumbGrabber, true);
  const popup = read('popup.html');
  assert.ok(popup.includes('data-key="thumbGrabber"'), 'popup lacks the thumbGrabber toggle');
});
t('manifest: thumbgrab.js + thumbgrab.css registered on the youtube.com script, count stays 3', () => {
  const m = JSON.parse(read('manifest.json'));
  assert.equal(m.content_scripts.length, 3, 'content_scripts count must stay 3 (append, do not add an entry)');
  const yt = m.content_scripts.find(cs => (cs.js || []).includes('content/yt.js'));
  assert.ok(yt, 'the youtube.com content script entry is missing');
  assert.ok(yt.matches.includes('https://www.youtube.com/*'));
  assert.ok(yt.js.includes('content/thumbgrab.js'), 'thumbgrab.js is not registered');
  assert.ok((yt.css || []).includes('content/thumbgrab.css'), 'thumbgrab.css is not registered');
  assert.ok(yt.js.indexOf('lib/common.js') < yt.js.indexOf('content/thumbgrab.js'), 'common.js must load before thumbgrab.js');
  assert.ok(exists('content/thumbgrab.js') && exists('content/thumbgrab.css'), 'new files must exist');
});
t('thumbgrab.js: IIFE, api shim, no import/export/require, no stray control bytes', () => {
  const src = read('content/thumbgrab.js');
  const firstCode = src.split('\n').find(l => l.trim() && !/^\s*(\/\/|\*|\/\*)/.test(l)) || '';
  assert.ok(/^\s*\((\(\)\s*=>|function)/.test(firstCode), 'should start with an IIFE, got: ' + firstCode);
  assert.ok(/\)\(\);\s*$/.test(src.trim()), 'should end by invoking the IIFE');
  assert.ok(src.includes('globalThis.browser ?? globalThis.chrome'), 'missing the browser/chrome shim');
  const code = src.split('\n').filter(l => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
  assert.ok(!/^\s*(import|export)\b/m.test(code), 'has an import/export statement');
  assert.ok(!/\brequire\s*\(/.test(code), 'uses require()');
  for (let i = 0; i < src.length; i++) { const c = src.charCodeAt(i); assert.ok(c >= 32 || c === 9 || c === 10 || c === 13, 'stray control byte at ' + i); }
});
t('thumbgrab.css: only sf- prefixed class hooks', () => {
  const css = read('content/thumbgrab.css').replace(/\/\*[\s\S]*?\*\//g, '');
  for (const m of css.matchAll(/\.([a-zA-Z][\w-]*)/g)) assert.ok(m[1].startsWith('sf-'), 'class .' + m[1] + ' is not sf- prefixed');
});
t('bg.js: thumbBlob handler exists and is registered for the grabber', () => {
  const bg = read('bg.js');
  assert.ok(/function\s+thumbBlob\s*\(/.test(bg), 'bg.js is missing the thumbBlob function');
  assert.ok(/\n\s*thumbBlob,/.test(bg), 'thumbBlob is not registered in the handlers map');
});
t('parseVideoId: grabber-style thumbnail hrefs resolve, channel links do not', () => {
  assert.equal(SF.parseVideoId('/watch?v=' + ID + '&list=PLx&index=2'), ID);
  assert.equal(SF.parseVideoId('/shorts/' + ID), ID);
  assert.equal(SF.parseVideoId('https://www.youtube.com/watch?v=' + ID + '&pp=abc'), ID);
  assert.equal(SF.parseVideoId('/@SomeChannel'), null);
  assert.equal(SF.parseVideoId('/feed/subscriptions'), null);
});

/* ================================================================ runner */
(async () => {
  let passed = 0, failed = 0;
  const lines = [];
  for (const { name, fn } of tests) {
    try {
      const r = fn();
      if (r && typeof r.then === 'function') await r;
      passed++;
      lines.push('  ok   ' + name);
    } catch (e) {
      failed++;
      const msg = String((e && e.stack) || e).split('\n').slice(0, 4).join('\n         ');
      lines.push('  FAIL ' + name + '\n         ' + msg);
    }
  }
  console.log(lines.join('\n'));
  const total = passed + failed;
  if (failed) { console.log('\nFAILED ' + failed + '/' + total); process.exit(1); }
  console.log('\nALL PASS ' + passed + '/' + total);
})();
