/* VLGP Web platform v0 skeleton — session browse + 3-view frames +
 * IN/OUT trim + status + save (T10 migration baseline). */
'use strict';

const CAMERAS = ['camera_arm_2', 'camera_arm_1', 'camera_arm_3'];
const PREFETCH = 12;          // frames kept behind/around the playhead
const PRELOAD = 128;          // rows warmed AHEAD of the playhead (smooth playback)
const FETCH_CHUNK = 16;       // rows per network request per camera
const CRIT_WINDOW = 16;       // rows fetched per critical-path request (seek)
const MOTION_POINTS = 2000;   // motion-curve pixel budget (server downsample)
const FPS = 30;               // alignment grid rate — time axis MM:SS:FF
const MAX_W = { camera_arm_2: 640, camera_arm_1: 320, camera_arm_3: 320 };
const CURVE_COLORS = { arm1: '#ffb43c', arm2: '#c8c8c8', arm3: '#78dc78', arm4: '#c8c8c8' };
const GRIPPER_COLOR = '#7896ff';

// Inline SVG icons for the transport buttons — no font-glyph dependency
// (text glyphs like ▶/⏸ can render as tofu/emoji on Chinese Windows and
// their width change between states shifts the button under the mouse,
// which swallows the click and makes pause unreliable).
const ICONS = {
  play: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
        '<path d="M5 3.4v9.2l7.4-4.6z" fill="currentColor"/></svg>',
  pause: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
         '<rect x="4" y="3.4" width="2.8" height="9.2" rx="1" fill="currentColor"/>' +
         '<rect x="9.2" y="3.4" width="2.8" height="9.2" rx="1" fill="currentColor"/></svg>',
  prev: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
        '<path d="M10.6 3.6 5.8 8l4.8 4.4" fill="none" stroke="currentColor" ' +
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  next: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
        '<path d="M5.4 3.6l4.8 4.4-4.8 4.4" fill="none" stroke="currentColor" ' +
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const state = {
  session: null,
  nRows: 0,
  row: 0,
  playing: false,
  playTimer: null,
  playClock: 0,               // last performance.now() while playing
  playAccum: 0,               // fractional row accumulator (time-based advance)
  speed: 1,                   // 0.5 / 1 / 2 / 4
  trim: null,                 // {start_row, end_row}
  status: 'pending',
  annotations: null,
  motionRows: [],
  motionDpos: {},             // arm -> Float array (downsampled |dpos|)
  motionGripper: {},          // arm -> Float array (downsampled gripper_angle)
  curveArm: 'all',            // curve selection: 'all' | '1' | '2' | '3' | '4'
  arm: 1,                     // arm shown in the robot status panel
  rowStatus: null,            // latest /api/row/{row} payload
  statusTimer: null,          // debounce for row-status fetches
  frameCache: {},             // cam -> Map(row -> decoded Image) preview cache
  crit: {},                   // cam -> {inFlight} one coalesced critical fetch per cam
  lastDraw: {},               // cam -> last row actually drawn (render-loop dedup)
  rafId: null,                // requestAnimationFrame render-loop handle
  preloadTimer: null,         // lookahead preload scheduler handle
  preloadBusy: false,         // preload loop re-entry guard
  preloadGen: 0,              // invalidated on session switch
  lastMotionRow: -1,          // drawMotion throttle: last row the curve drew
  lastMotionAt: 0,            // drawMotion throttle: last curve draw timestamp
  preloadPct: -1,             // last rendered preload percentage (DOM dedup)
  serverPreload: null,        // server whole-session preload status (polled)
  preloadPollTimer: null,     // server preload status poller
};

const $ = (id) => document.getElementById(id);

function fmt(v, digits = 2) {
  if (v === null || v === undefined) return '—';
  if (Array.isArray(v)) return v.map((x) => fmt(x, digits)).join(', ');
  return typeof v === 'number' ? v.toFixed(digits) : String(v);
}

function fmtVec(v, digits = 1) { return v && v.length ? fmt(v, digits) : '—'; }

// quaternion [w,x,y,z] -> intrinsic ZYX euler angles in degrees
function quatToEulerDeg(q) {
  if (!q || q.some((x) => x === null)) return null;
  const [w, x, y, z] = q;
  const roll = Math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y));
  const sinp = 2 * (w * y - z * x);
  const pitch = Math.abs(sinp) >= 1 ? Math.sign(sinp) * Math.PI / 2 : Math.asin(sinp);
  const yaw = Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z));
  return [roll, pitch, yaw].map((v) => (v * 180 / Math.PI).toFixed(1));
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function loadSessions() {
  const { sessions } = await api('/api/sessions');
  const ul = $('session-list');
  ul.innerHTML = '';
  for (const s of sessions) {
    const li = document.createElement('li');
    const ann = s.annotations || {};
    const badge = s.has_alignment ? '' : '<span class="badge">未对齐</span>';
    li.innerHTML = `${s.name}${badge}<span class="sub">${s.n_rows} 行 · ${ann.status || 'pending'}` +
      (ann.trim ? ` · [${ann.trim.start_row}, ${ann.trim.end_row}]` : '') + '</span>';
    li.onclick = () => selectSession(s.name);
    ul.appendChild(li);
  }
}

// Generate the camera-anchor alignment table on first open of an unaligned
// session. POST starts an async job; the progress bar polls /align/status.
async function ensureAligned(name) {
  const msg = $('align-status');
  const bar = $('align-progress');
  const fill = $('align-progress-fill');
  msg.textContent = '正在生成对齐轴（首次打开未对齐会话，自动对齐中）…';
  bar.hidden = false;
  fill.style.width = '0%';
  let r;
  try {
    r = await fetch(`/api/sessions/${name}/align`, { method: 'POST' });
  } catch (e) {
    msg.textContent = '对齐请求失败: ' + e.message;
    bar.hidden = true;
    throw e;
  }
  let body;
  try { body = await r.json(); } catch { body = {}; }
  if (!r.ok) {
    const detail = body && body.detail;
    const reason = (detail && detail.error) || body.error || `${r.status} ${r.statusText}`;
    msg.textContent = '';
    bar.hidden = true;
    throw new Error(reason);
  }
  if (body.status === 'already_aligned') {
    msg.textContent = '';
    bar.hidden = true;
    return body;
  }
  // poll progress until done / error
  for (;;) {
    await new Promise((res) => setTimeout(res, 400));
    let st;
    try { st = await api(`/api/sessions/${name}/align/status`); } catch { continue; }
    if (st.status === 'done') {
      msg.textContent = `对齐完成：${st.result.n_rows} 行 (${st.elapsed_s}s)`;
      bar.hidden = true;
      setTimeout(() => { if (!state.playing) msg.textContent = ''; }, 5000);
      return st.result;
    }
    if (st.status === 'error') {
      msg.textContent = '';
      bar.hidden = true;
      throw new Error(st.error || '对齐失败');
    }
    const pct = st.total ? Math.round(st.current / st.total * 100) : 0;
    fill.style.width = pct + '%';
    msg.textContent = `${st.message || '对齐中'}… ${st.total ? `${st.current}/${st.total}` : ''} (${pct}%)`;
  }
}

async function selectSession(name) {
  state.session = name;
  state.row = 0;
  state.playing = false;
  stopPlay();
  // invalidate the previous session's preload/cache before loading the new one
  state.preloadGen += 1;
  state.preloadTimer = null;
  state.frameCache = {};
  state.crit = {};
  state.lastDraw = {};
  state.preloadPct = -1;
  state.serverPreload = null;
  clearTimeout(state.preloadPollTimer);
  $('preload-bar').hidden = true;
  for (const cam of CAMERAS) {
    const cv = $(`cam-${cam.slice(-1)}`);
    if (cv) cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
  }
  try {
    let info = await api(`/api/sessions/${name}`);
    if (!info.n_rows) {
      await ensureAligned(name);                       // on-demand alignment
      info = await api(`/api/sessions/${name}`);
      if (!info.n_rows) throw new Error('对齐生成后仍无可用的对齐表');
    }
    state.nRows = info.n_rows;
    state.annotations = info.annotations || {};
    state.trim = state.annotations.trim || null;
    state.status = state.annotations.status || 'pending';
    $('session-name').textContent = name;
    document.querySelectorAll('#session-list li').forEach(
      (li) => li.classList.toggle('active', li.textContent.startsWith(name)));
    $('slider').max = Math.max(0, state.nRows - 1);
    $('slider').value = 0;
    $('btn-in').disabled = $('btn-out').disabled = false;
    $('btn-save').disabled = false;
    $('status').disabled = false;
    $('status').value = state.status;
    $('curve-bar').hidden = false;
    await loadMotion();
    updateStatusLabel();
    updateTrimInfo();
    buildArmTabs();
    await showRow(0);
    startServerPreload(name);                          // whole-session preload
    loadSessions();                                    // refresh badge
  } catch (err) {
    alert('无法打开会话: ' + err.message);
    state.session = null;
    state.nRows = 0;
    $('session-name').textContent = name + ' (不可用)';
    $('btn-in').disabled = $('btn-out').disabled = $('btn-save').disabled = true;
    $('status').disabled = true;
    $('curve-bar').hidden = true;
    $('preload-bar').hidden = true;
    state.preloadGen += 1;
    state.preloadTimer = null;
    state.frameCache = {};
    state.crit = {};
    state.lastDraw = {};
    state.serverPreload = null;
    clearTimeout(state.preloadPollTimer);
  }
}

async function loadMotion() {
  const m = await api(`/api/sessions/${state.session}/alignment?points=${MOTION_POINTS}`);
  state.motionRows = m.rows;
  state.motionDpos = m.dpos || {};
  state.motionGripper = m.gripper || {};
  drawMotion();
}

function drawMotion() {
  const cv = $('motion'), ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  const rows = state.motionRows;
  if (!rows.length) return;
  const showAll = state.curveArm === 'all';
  // arm selection: show every arm, or only the chosen one
  const armKeys = showAll
    ? Object.keys(state.motionDpos)
    : (state.motionDpos['arm' + state.curveArm] ? ['arm' + state.curveArm] : []);
  // PyQt5 parity: each series normalised to its own max.
  for (const arm of armKeys) {
    const v = state.motionDpos[arm];
    const mx = Math.max(...v, 1e-9);
    ctx.strokeStyle = CURVE_COLORS[arm] || '#c8c8c8';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < v.length; i++) {
      const x = (i / (v.length - 1)) * cv.width;
      const y = cv.height - 6 - (v[i] / mx) * (cv.height - 14);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  // gripper arm1 on top (blue) — PyQt5 _redraw_curve draws it too
  const g = state.motionGripper['arm1'];
  if (g && g.length && (showAll || state.curveArm === 1)) {
    const mx = Math.max(...g, 1e-9);
    ctx.strokeStyle = GRIPPER_COLOR;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < g.length; i++) {
      const x = (i / (g.length - 1)) * cv.width;
      const y = cv.height - 6 - (g[i] / mx) * (cv.height - 14);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  // trim IN/OUT markers
  if (state.trim && state.nRows > 1) {
    for (const [key, color] of [['start_row', 'rgba(120,220,120,0.9)'],
                                 ['end_row', 'rgba(240,120,120,0.9)']]) {
      const x = (state.trim[key] / (state.nRows - 1)) * cv.width;
      ctx.strokeStyle = color;
      ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, cv.height); ctx.stroke();
      ctx.setLineDash([]);
    }
  }
  // current-row marker
  if (state.row >= 0 && state.nRows > 1) {
    const x = (state.row / (state.nRows - 1)) * cv.width;
    ctx.strokeStyle = '#ffffff';
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, cv.height); ctx.stroke();
  }
  // legend reflects the current selection
  const legend = [];
  if (showAll) {
    legend.push(['#ffb43c', 'arm1 dpos'], ['#78dc78', 'arm3 dpos'],
                ['#c8c8c8', 'arm2/4 dpos']);
    if (g && g.length) legend.push([GRIPPER_COLOR, 'gripper arm1']);
  } else if (armKeys.length) {
    const arm = armKeys[0];
    legend.push([CURVE_COLORS[arm] || '#c8c8c8', `${arm} dpos`]);
    if (arm === 'arm1' && g && g.length) legend.push([GRIPPER_COLOR, 'gripper arm1']);
  }
  let lx = 8;
  ctx.font = '11px "Microsoft YaHei", sans-serif';
  for (const [color, label] of legend) {
    ctx.fillStyle = color;
    ctx.fillRect(lx, 6, 14, 3);
    ctx.fillStyle = '#a8b4c2';
    ctx.fillText(label, lx + 18, 11);
    lx += 18 + ctx.measureText(label).width + 14;
  }
}

// --------------------------------------------------- frame cache + preload
// Decoded previews are cached client-side (cam -> Map(row -> Image)) so a
// playback tick draws instantly from memory instead of round-tripping the
// server. A background lookahead loop keeps [row-PREFETCH, row+PRELOAD]
// warmed; the server encodes batches in parallel (~190 rows/s), far above
// the 30 rows/s playback rate.

function frameCache(cam) {
  let c = state.frameCache[cam];
  if (!c) { c = new Map(); state.frameCache[cam] = c; }
  return c;
}

// ------------------------------------------------------- render loop (rAF)
// Rendering is fully decoupled from fetching: a requestAnimationFrame loop
// draws, for each camera, the cached frame of the current counter row — or,
// when the preload has not caught up yet, the nearest cached row before it.
// The video therefore lags gracefully instead of freezing on cache misses,
// and rendering never blocks on the network. The same loop drives the
// preload progress bar.
function startRenderLoop() {
  if (state.rafId) return;
  const tick = () => {
    state.rafId = requestAnimationFrame(tick);
    if (!state.session || !state.nRows) return;
    for (const cam of CAMERAS) {
      const cache = state.frameCache[cam];
      if (!cache || !cache.size) continue;
      let r = state.row;
      while (r >= 0 && !cache.has(r)) r--;
      if (r < 0) continue;
      const im = cache.get(r);
      const cv = $(`cam-${cam.slice(-1)}`);
      if (!cv) continue;
      if (!(im.complete && im.naturalWidth > 0)) {
        im.addEventListener('load', () => { state.lastDraw[cam] = -1; }, { once: true });
        continue;
      }
      if (state.lastDraw[cam] === r) continue;
      cv.getContext('2d').drawImage(im, 0, 0, cv.width, cv.height);
      state.lastDraw[cam] = r;
    }
    updatePreloadBar();
  };
  state.rafId = requestAnimationFrame(tick);
}

function stopRenderLoop() {
  if (state.rafId) { cancelAnimationFrame(state.rafId); state.rafId = null; }
}

// Share of the lookahead window [row, row+PRELOAD] already cached, for the
// least-ready camera (the honest answer to "can I play smoothly yet").
function preloadProgress() {
  let worst = 1;
  for (const cam of CAMERAS) {
    const c = state.frameCache[cam];
    const win = Math.min(PRELOAD, Math.max(0, state.nRows - 1 - state.row)) + 1;
    if (win <= 0) return 1;
    let have = 0;
    if (c) {
      for (let r = state.row; r <= state.row + win - 1; r++) {
        if (c.has(r)) have++;
      }
    }
    worst = Math.min(worst, have / win);
  }
  return worst;
}

// Progress bar: "预加载 X% · 第 N/M 段" driven by the SERVER's whole-session
// preload (that is what makes every seek fast); falls back to the local
// sliding-window metric when the server has no preload endpoint (old code).
// Hidden once the whole session is preloaded — smooth playback everywhere.
function updatePreloadBar() {
  const sp = state.serverPreload;
  let pct, label;
  if (sp && sp.status === 'running') {
    pct = Math.round((sp.current / Math.max(1, sp.total)) * 100);
    label = sp.segments > 1
      ? `整段预加载 ${pct}% · 第 ${Math.min(sp.segment + 1, sp.segments)}/${sp.segments} 段`
      : `整段预加载 ${pct}%`;
  } else if (sp && sp.status === 'done') {
    pct = 100;
  } else {
    pct = Math.round(preloadProgress() * 100);   // fallback: local window
    label = `预加载 ${pct}%`;
  }
  if (pct === state.preloadPct) return;
  state.preloadPct = pct;
  const bar = $('preload-bar'), fill = $('preload-fill'), txt = $('preload-text');
  if (!bar || !fill || !txt) return;
  if (pct >= 100) { bar.hidden = true; return; }
  bar.hidden = false;
  fill.style.width = pct + '%';
  fill.classList.toggle('warming', pct < 50);
  txt.textContent = label;
}

// Kick off the server's whole-session preview preload and poll its progress.
// Fire-and-forget: if the endpoint is missing (old server) the bar falls
// back to the local-window metric.
function startServerPreload(name) {
  clearTimeout(state.preloadPollTimer);
  (async () => {
    try {
      const r = await fetch(`/api/sessions/${name}/preload`, { method: 'POST' });
      if (!r.ok) return;
      const st = await r.json();
      if (state.session !== name) return;
      state.serverPreload = st;
      pollServerPreload(name);
    } catch { /* fall back to the local-window metric */ }
  })();
}

function pollServerPreload(name) {
  clearTimeout(state.preloadPollTimer);
  state.preloadPollTimer = setTimeout(async () => {
    if (state.session !== name) return;
    try {
      const st = await api(`/api/sessions/${name}/preload/status`);
      if (state.session !== name) return;
      state.serverPreload = st;
      if (st.status === 'running') pollServerPreload(name);
    } catch { /* old server; stop polling, bar falls back */ }
  }, 600);
}

// ------------------------------------------------- binary frame batch fetch
// Uses /frames/batch-bin (raw JPEGs) -> Blob -> object URL -> Image. The
// ArrayBuffer arrives without base64/JSON, so a preload step costs ~1ms on
// the main thread instead of ~30ms of JSON.parse. The cache Map is captured
// at call time, so a response that lands after a session switch writes into
// the discarded old Map and can never pollute the new session.
async function fetchChunk(cam, rows, sess) {
  const cache = frameCache(cam);
  try {
    const r = await fetch(`/api/sessions/${sess}/frames/batch-bin`,
                          { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ cam, rows, max_width: MAX_W[cam] }) });
    if (!r.ok) return 0;
    const buf = new Uint8Array(await r.arrayBuffer());
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    const count = dv.getInt32(0, true);
    let off = 4, added = 0;
    for (let i = 0; i < count; i++) {
      const row = dv.getInt32(off, true);
      const len = dv.getInt32(off + 4, true);
      off += 8;
      const bytes = buf.subarray(off, off + len);
      off += len;
      if (cache.has(row)) continue;
      const url = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }));
      const im = new Image();
      im._url = url;                       // revoked on eviction
      im.src = url;
      cache.set(row, im);
      added++;
    }
    return added;
  } catch { return 0; }
}

// ----------------------------------------- critical-path fetch (per camera)
// Fills the playhead row as soon as it is needed, then widens the window for
// smooth playback right after a seek. Targets queue up: while a fetch is in
// flight, newer requests only update the target (latest wins — forward AND
// backward), and the fetch re-runs for the latest target on completion, so a
// rapid slider drag converges on the final position whether playing or not.
function criticalFetch(cam, row) {
  const c = state.crit[cam] || (state.crit[cam] = { inFlight: false, target: -1, failStreak: 0 });
  c.target = row;               // latest requested row wins (seek both ways)
  if (c.inFlight) return;
  c.inFlight = true;
  runCritical(cam, c);
}

async function runCritical(cam, c) {
  const sess = state.session;
  let added = 0;
  try {
    const row = c.target;
    // stage 1: the exact target row alone — first paint as fast as possible
    if (state.session === sess && !frameCache(cam).has(row)) {
      added += await fetchChunk(cam, [row], sess);
    }
    // stage 2: widen ahead of the playhead so playback after the seek is smooth
    if (state.session === sess) {
      const rows = [];
      for (let r = row + 1; r <= Math.min(state.nRows - 1, row + CRIT_WINDOW); r++) {
        if (!frameCache(cam).has(r)) rows.push(r);
      }
      if (rows.length) added += await fetchChunk(cam, rows, sess);
    }
  } catch { /* preview is best-effort */ }
  finally {
    c.inFlight = false;
    if (added > 0) c.failStreak = 0; else c.failStreak += 1;
    // re-run for a newer queued target, or while playing and the playhead
    // outran the window (bounded so a broken session cannot spin forever)
    const need = state.session === sess && c.failStreak < 8
      && !frameCache(cam).has(state.row)
      && (c.target !== row || state.playing);
    if (need) runCritical(cam, c);
  }
}

function ensureRowCached(row) {
  for (const cam of CAMERAS) {
    if (!frameCache(cam).has(row)) criticalFetch(cam, row);
  }
}

// start (or continue) the lookahead preloader; re-entry is guarded by
// preloadBusy so rapid row changes cannot stack overlapping fetches
function kickPreload() {
  if (state.preloadBusy || !state.session) return;
  state.preloadBusy = true;
  preloadStep(state.preloadGen);
}

async function preloadStep(gen) {
  let didWork = false;
  try {
    if (gen !== state.preloadGen || !state.session) return;
    const row = state.row;
    const lo = Math.max(0, row - PREFETCH);
    const hi = Math.min(state.nRows - 1, row + PRELOAD);
    const jobs = [];
    for (const cam of CAMERAS) {
      const c = frameCache(cam);
      // forward from the playhead first (covers the video about to be seen),
      // then the behind-window — after a seek the playhead row is filled
      // before rows before it
      const miss = [];
      for (let r = row; r <= hi; r++) if (!c.has(r)) miss.push(r);
      for (let r = lo; r < row; r++) if (!c.has(r)) miss.push(r);
      if (!miss.length) continue;
      didWork = true;
      jobs.push(fetchChunk(cam, miss.slice(0, FETCH_CHUNK), state.session));
    }
    if (jobs.length) await Promise.all(jobs);
    if (gen !== state.preloadGen) return;
    // bound memory: drop rows outside the current window (stale after seeks)
    for (const cam of CAMERAS) {
      const c = frameCache(cam);
      for (const k of Array.from(c.keys())) {
        if (k < lo || k > hi) {
          const im = c.get(k);
          c.delete(k);
          if (im && im._url) URL.revokeObjectURL(im._url);
        }
      }
    }
  } catch { /* preload is best-effort */ }
  finally {
    state.preloadBusy = false;
    if (gen === state.preloadGen && state.session) {
      // pace by how far the window still is from full: catch up fast after a
      // seek, go gentle while nearly warm (keeps background CPU away from the
      // renderer during playback)
      const ahead = preloadProgress();
      const gap = ahead >= 1 ? 400 : (ahead < 0.35 ? 40 : 150);
      state.preloadTimer = setTimeout(() => kickPreload(), didWork ? gap : 300);
    }
  }
}

// Redraw the motion curve at most every ~3 rows (or 150ms) while playing —
// the marker moves imperceptibly over such a short distance but redrawing
// ~8000 path segments per 33ms tick adds real main-thread cost on a machine
// where the server is concurrently encoding preview frames.
function drawMotionIfNeeded() {
  const now = performance.now();
  if (state.playing
      && Math.abs(state.row - state.lastMotionRow) < 3
      && now - state.lastMotionAt < 150) return;
  state.lastMotionRow = state.row;
  state.lastMotionAt = now;
  drawMotion();
}

// ------------------------------------------------------------------ time axis
// Row index -> MM:SS:FF (frames within the second, 30 fps grid).
function fmtTime(row) {
  row = Math.max(0, row);
  const f = row % FPS;
  const s = Math.floor(row / FPS) % 60;
  const m = Math.floor(row / (FPS * 60));
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}:${String(f).padStart(2, '0')}`;
}

// Tick interval (seconds) so the ruler stays readable (~14 labels max).
function rulerStep(dur) {
  for (const s of [10, 30, 60, 120, 300, 600]) {
    if (dur / s <= 14) return s;
  }
  return 900;
}

// Ruler tick label "M:SS" (no frame component — frame readout is separate).
function fmtTick(t) {
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
}

// Time ruler above the playback slider: MM:SS tick marks + a white marker
// at the current position. Small canvas — safe to redraw every showRow.
function drawTimeRuler() {
  const cv = $('time-ruler');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  const n = state.nRows;
  if (n < 2) return;
  const dur = n / FPS;
  const step = rulerStep(dur);
  const span = cv.width - 4;
  ctx.font = '9px "Microsoft YaHei", sans-serif';
  ctx.fillStyle = '#8fa3bf';
  ctx.strokeStyle = '#4a5568';
  for (let t = 0; t <= dur + 1e-9; t += step) {
    const x = 2 + (Math.min(t, dur) / dur) * span;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 6); ctx.stroke();
    ctx.fillText(fmtTick(t), x + 3, 12);
  }
  const mx = 2 + (state.row / (n - 1)) * span;
  ctx.strokeStyle = '#ffffff';
  ctx.beginPath(); ctx.moveTo(mx, 0); ctx.lineTo(mx, cv.height); ctx.stroke();
}

// Update the playhead: counter, curve marker, cache warming. Rendering is
// handled by the rAF loop, so this never waits on the network.
function showRow(row) {
  row = Math.max(0, Math.min(state.nRows - 1, row));
  state.row = row;
  $('slider').value = row;
  $('row-info').textContent = `row ${row} / ${state.nRows - 1}`;
  const ti = $('time-info');
  if (ti) ti.textContent = fmtTime(row);
  drawMotionIfNeeded();
  drawTimeRuler();
  ensureRowCached(row);
  scheduleRowStatus(row);
  kickPreload();
}

// -------------------------------------------------------- robot status panel
async function fetchRowStatus(row) {
  try {
    state.rowStatus = await api(`/api/sessions/${state.session}/row/${row}`);
    renderArmStatus();
  } catch { /* row may be out of range mid-prefetch; ignore */ }
}

function scheduleRowStatus(row) {
  clearTimeout(state.statusTimer);
  // the side panel is secondary during playback — refresh less often then
  const delay = state.playing ? 400 : 150;
  state.statusTimer = setTimeout(() => fetchRowStatus(row), delay);
}

function buildArmTabs() {
  const tabs = $('arm-tabs');
  tabs.innerHTML = '';
  for (let a = 1; a <= 4; a++) {
    const b = document.createElement('button');
    b.className = 'arm-tab' + (a === state.arm ? ' active' : '');
    b.textContent = `arm${a}`;
    b.onclick = () => { state.arm = a; renderArmStatus(); };
    tabs.appendChild(b);
  }
}

function renderArmStatus() {
  document.querySelectorAll('.arm-tab').forEach((b, i) => {
    b.classList.toggle('active', i + 1 === state.arm);
  });
  const box = $('arm-status');
  const rs = state.rowStatus;
  const a = rs && rs.arms ? rs.arms[`arm${state.arm}`] : null;
  if (!a) { box.innerHTML = '<span class="v na">暂无数据</span>'; return; }
  const euler = quatToEulerDeg(a.orientation_wxyz);
  const rows = [
    ['位置 (mm)', fmtVec(a.position_mm)],
    ['镜系位置 (mm)', fmtVec(a.scope_position_mm)],
    ['四元数 (wxyz)', fmtVec(a.orientation_wxyz, 4)],
    ['欧拉角 (deg)', euler ? euler.join(', ') : '—'],
    ['夹爪角度', fmt(a.gripper_angle, 2)],
    ['本步位移 (mm)', fmt(a.dpos_mm, 3)],
    ['器械', a.instrument || '—'],
    ['能量状态', fmt(a.energy_instrument_status, 0)],
  ];
  box.innerHTML = rows.map(([k, v]) =>
    `<span class="k">${k}</span><span class="v${v === '—' ? ' na' : ''}">${v}</span>`
  ).join('');
}

function setTrim(which) {
  if (!state.trim) state.trim = { start_row: state.row, end_row: state.row };
  if (which === 'in') state.trim.start_row = state.row;
  else state.trim.end_row = state.row;
  if (state.trim.start_row > state.trim.end_row)
    [state.trim.start_row, state.trim.end_row] = [state.trim.end_row, state.trim.start_row];
  updateTrimInfo();
}

function updateTrimInfo() {
  $('trim-info').textContent = state.trim
    ? `IN=${state.trim.start_row} OUT=${state.trim.end_row}` : '未设置 IN/OUT';
}

function updateStatusLabel() {
  const lbl = $('status-label');
  lbl.textContent = `status: ${state.status}`;
  lbl.className = state.status;
}

async function saveAnnotations() {
  if (!state.trim) { alert('请先设置 IN/OUT'); return; }
  const ann = {
    version: 1,
    reviewed_by: '',                       // 服务端补（PyQt5 同 USERNAME）
    reviewed_at: '',                       // 服务端补（本地时区 ISO 秒级）
    status: state.status,
    reject_reason: state.status === 'rejected' ? 'rejected in review' : '',
    trim: state.trim,
    segments: [],
    flags: [],
    notes: '',
  };
  const r = await fetch(`/api/sessions/${state.session}/annotations`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(ann),
  });
  if (!r.ok) {
    const err = await r.json();
    alert('保存失败: ' + JSON.stringify(err.detail || err));
    return false;
  }
  const res = await r.json();
  state.annotations = res.saved;
  alert('已保存 annotations.json');
  loadSessions();
  return true;
}

async function nextSession() {
  // PyQt5 parity (N): save current (if trimmed) then jump to next pending.
  if (state.trim && state.session) {
    const ok = await saveAnnotations();
    if (!ok) return;
  }
  let queue;
  try { queue = await api('/api/review/queue'); }
  catch { alert('无法读取评审队列'); return; }
  const pending = queue.pending || [];
  const rest = pending.filter((n) => n !== state.session);
  if (!rest.length) { alert('没有更多待评审会话 (全部 approved/rejected)'); return; }
  await selectSession(rest[0]);
}

function step(delta) {
  const next = Math.max(0, Math.min(state.nRows - 1, state.row + delta));
  if (next !== state.row) showRow(next);
  return next === state.nRows - 1;
}

// PyQt5 parity: time-based advance on the 30 Hz grid (rows += dt*speed*30),
// so wall-clock playback tracks the selected speed regardless of timer jitter.
function playTick() {
  const now = performance.now();
  const dt = (now - state.playClock) / 1000;
  state.playClock = now;
  state.playAccum += dt * state.speed * 30.0;
  const stepN = Math.floor(state.playAccum);
  if (stepN < 1) return;
  state.playAccum -= stepN;
  if (step(stepN)) {
    stopPlay();
    showRow(state.nRows - 1);   // ensure the final row is fetched and rendered
  }
}

function stopPlay() {
  if (state.playTimer) { clearInterval(state.playTimer); state.playTimer = null; }
  state.playing = false;
  const b = $('btn-play');
  if (b) b.innerHTML = ICONS.play;
}

function togglePlay() {
  if (state.playing) {
    const snapRow = state.row;
    stopPlay();
    // the render loop shows the nearest cached frame; make sure the exact
    // paused row is fetched so the picture settles on it (cache hit = no-op)
    if (state.nRows > 0) ensureRowCached(snapRow);
    return;
  }
  if (state.row >= state.nRows - 1) state.row = 0;
  state.playing = true;
  state.playClock = performance.now();
  state.playAccum = 0;
  $('btn-play').innerHTML = ICONS.pause;
  state.playTimer = setInterval(playTick, 33);
}

// ---- wire up ----
$('btn-in').onclick = () => setTrim('in');
$('btn-out').onclick = () => setTrim('out');
$('btn-prev').onclick = () => step(-1);
$('btn-next').onclick = () => step(1);
$('btn-play').onclick = togglePlay;
$('btn-save').onclick = saveAnnotations;
$('btn-next-session').onclick = nextSession;
// fixed-size inline SVG icons (no font-glyph tofu, no layout shift on click)
$('btn-play').innerHTML = ICONS.play;
$('btn-prev').innerHTML = ICONS.prev;
$('btn-next').innerHTML = ICONS.next;
// motion-curve arm selector
document.querySelectorAll('.curve-btn').forEach((b) => {
  b.onclick = () => {
    state.curveArm = b.dataset.arm;   // 'all' | '1' | '2' | '3' | '4'
    document.querySelectorAll('.curve-btn').forEach((x) =>
      x.classList.toggle('active', x === b));
    drawMotion();
  };
});
$('slider').oninput = (e) => { stopPlay(); showRow(parseInt(e.target.value, 10)); };
$('speed').onchange = (e) => { state.speed = parseFloat(e.target.value); };
$('status').onchange = (e) => { state.status = e.target.value; updateStatusLabel(); };
$('motion').onclick = (e) => {
  if (!state.nRows) return;
  const x = e.offsetX, w = $('motion').width;
  const row = Math.round((x / w) * (state.nRows - 1));
  stopPlay(); showRow(row);
};
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  const jump = Math.max(1, Math.round(state.speed * 5));
  if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === 'ArrowRight') step(1);
  else if (e.key === 'j' || e.key === 'J') { stopPlay(); step(-jump); }
  else if (e.key === 'l' || e.key === 'L') { stopPlay(); step(jump); }
  else if (e.key === ' ') { e.preventDefault(); togglePlay(); }
  else if (e.key === '[') setTrim('in');
  else if (e.key === ']') setTrim('out');
  else if (e.key === '1' || e.key === '2' || e.key === '3') {
    state.status = ['approved', 'rejected', 'pending'][parseInt(e.key, 10) - 1];
    $('status').value = state.status; updateStatusLabel();
  } else if (e.key === 's' || e.key === 'S') saveAnnotations();
  else if (e.key === 'n' || e.key === 'N') nextSession();
});

loadSessions();
startRenderLoop();
