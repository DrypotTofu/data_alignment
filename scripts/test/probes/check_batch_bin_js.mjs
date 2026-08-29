// Verify the client's batch-bin parsing logic against the real server.
// Mirrors fetchChunk's DataView parsing (no DOM needed).
const BASE = 'http://127.0.0.1:8765/api/sessions/test_20260828_160624';

async function parseBin(cam, rows) {
  const r = await fetch(`${BASE}/frames/batch-bin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cam, rows, max_width: cam === 'camera_arm_2' ? 640 : 320 }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const buf = new Uint8Array(await r.arrayBuffer());
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const count = dv.getInt32(0, true);
  let off = 4;
  const out = [];
  for (let i = 0; i < count; i++) {
    const row = dv.getInt32(off, true);
    const len = dv.getInt32(off + 4, true);
    off += 8;
    const bytes = buf.subarray(off, off + len);
    off += len;
    // JPEG magic check
    out.push({ row, len, magic: [bytes[0].toString(16), bytes[1].toString(16)] });
  }
  return out;
}

(async () => {
  for (const cam of ['camera_arm_2', 'camera_arm_1', 'camera_arm_3']) {
    const items = await parseBin(cam, [100, 101, 102, 103]);
    console.log(cam, 'count=' + items.length,
      items.map((i) => `row${i.row}:${i.len}B:${i.magic.join('')}`).join(' '));
  }
  // a wide seek window
  const t = Date.now();
  const items = await parseBin('camera_arm_2', Array.from({ length: 16 }, (_, i) => 8000 + i));
  console.log('seek16 arm2: ' + (Date.now() - t) + ' ms, count=' + items.length);
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
