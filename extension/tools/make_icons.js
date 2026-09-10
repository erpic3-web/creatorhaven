/* CreatorHaven — tools/make_icons.js
 * Writes icons/icon16.png, icon48.png, icon128.png with a minimal PNG encoder
 * (RGBA, zlib via node:zlib, CRC32 table) — no npm packages.
 * Motif: rounded orange square with a white pixel-font "SF".
 * Run: node tools/make_icons.js
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td), 0);
  return Buffer.concat([len, td, crc]);
}

function encodePng(w, h, rgba) {
  const sig = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8;   // bit depth
  ihdr[9] = 6;   // colour type RGBA
  ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  const stride = w * 4 + 1;
  const raw = Buffer.alloc(stride * h);
  for (let y = 0; y < h; y++) {
    raw[y * stride] = 0;                       // filter: none
    rgba.copy(raw, y * stride + 1, y * w * 4, (y + 1) * w * 4);
  }
  return Buffer.concat([sig, chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
}

// 5x7 pixel glyphs.
const GLYPHS = {
  S: ['.###.', '#...#', '#....', '.###.', '....#', '#...#', '.###.'],
  F: ['#####', '#....', '#....', '####.', '#....', '#....', '#....']
};

function draw(size) {
  const px = Buffer.alloc(size * size * 4);
  const half = size / 2;
  const r = Math.max(2, size * 0.22);
  const top = [0xF2, 0x6B, 0x1F], bottom = [0xC4, 0x43, 0x00];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const cx = x + 0.5, cy = y + 0.5;
      const qx = Math.max(Math.abs(cx - half) - (half - r), 0);
      const qy = Math.max(Math.abs(cy - half) - (half - r), 0);
      const d = Math.sqrt(qx * qx + qy * qy);
      const a = Math.max(0, Math.min(1, r + 0.5 - d));        // 1 px anti-aliased edge
      const t = y / (size - 1);
      const o = (y * size + x) * 4;
      px[o] = Math.round(top[0] + (bottom[0] - top[0]) * t);
      px[o + 1] = Math.round(top[1] + (bottom[1] - top[1]) * t);
      px[o + 2] = Math.round(top[2] + (bottom[2] - top[2]) * t);
      px[o + 3] = Math.round(a * 255);
    }
  }
  // "SF" centred, scale s (11 cells wide incl. a 1-cell gap, 7 tall).
  const s = Math.max(1, Math.floor(size * 0.75 / 11));
  const gw = 11 * s, gh = 7 * s;
  const x0 = Math.floor((size - gw) / 2), y0 = Math.floor((size - gh) / 2);
  const letters = [['S', 0], ['F', 6 * s]];
  for (const [g, dx] of letters) {
    const rows = GLYPHS[g];
    for (let gy = 0; gy < 7; gy++) {
      for (let gx = 0; gx < 5; gx++) {
        if (rows[gy][gx] !== '#') continue;
        for (let yy = 0; yy < s; yy++) {
          for (let xx = 0; xx < s; xx++) {
            const X = x0 + dx + gx * s + xx, Y = y0 + gy * s + yy;
            if (X < 0 || Y < 0 || X >= size || Y >= size) continue;
            const o = (Y * size + X) * 4;
            px[o] = 255; px[o + 1] = 255; px[o + 2] = 255; px[o + 3] = 255;
          }
        }
      }
    }
  }
  return px;
}

const outDir = path.resolve(__dirname, '..', 'icons');
fs.mkdirSync(outDir, { recursive: true });
for (const size of [16, 48, 128]) {
  const png = encodePng(size, size, draw(size));
  const file = path.join(outDir, 'icon' + size + '.png');
  fs.writeFileSync(file, png);
  console.log('wrote', file, png.length, 'bytes');
}
