/* VLGP Web platform v0 skeleton — session browse + 3-view frames +
 * IN/OUT trim + status + save (T10 migration baseline). */
'use strict';

const CAMERAS = ['camera_arm_2', 'camera_arm_1', 'camera_arm_3'];
const PREFETCH = 12;          // frames decoded ahead/behind per camera
const MOTION_POINTS = 2000;   // motion-curve pixel budget (server downsample)
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
  frameAbort: null,           // AbortController for the in-flight frame batch
  drawGen: 0,                 // generation counter: stale async draws are dropped
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
    loadSessions();                                    // refresh badge
  } catch (err) {
    alert('无法打开会话: ' + err.message);
    state.session = null;
    state.nRows = 0;
    $('session-name').textContent = name + ' (不可用)';
    $('btn-in').disabled = $('btn-out').disabled = $('btn-save').disabled = true;
    $('status').disabled = true;
    $('curve-bar').hidden = true;
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

async function showRow(row) {
  row = Math.max(0, Math.min(state.nRows - 1, row));
  state.row = row;
  $('slider').value = row;
  $('row-info').textContent = `row ${row} / ${state.nRows - 1}`;
  drawMotion();
  const gen = ++state.drawGen;
  // cancel any in-flight batch (stale during playback; critical on pause so
  // the frame fetch that was started just before the click cannot draw after)
  if (state.frameAbort) state.frameAbort.abort();
  const ac = new AbortController();
  state.frameAbort = ac;
  const rows = [];
  for (let r = row - PREFETCH; r <= row + PREFETCH; r++) {
    if (r >= 0 && r < state.nRows) rows.push(r);
  }
  const frames = {};
  try {
    await Promise.all(CAMERAS.map(async (cam) => {
      const body = { cam, rows, max_width: MAX_W[cam] };
      const res = await api(`/api/sessions/${state.session}/frames/batch`,
                            { method: 'POST', headers: { 'Content-Type': 'application/json' },
                              body: JSON.stringify(body), signal: ac.signal });
      frames[cam] = res.images || {};
    }));
  } catch (e) {
    if (e && e.name === 'AbortError') return;  // superseded by a newer row / paused
    return;                                    // preview is best-effort
  }
  if (ac.signal.aborted || gen !== state.drawGen) return;  // a newer row took over
  for (const cam of CAMERAS) {
    const img = frames[cam][String(row)];
    if (img) {
      const cv = $(`cam-${cam.slice(-1)}`);
      const ctx = cv.getContext('2d');
      const im = new Image();
      im.onload = () => { if (gen === state.drawGen) ctx.drawImage(im, 0, 0, cv.width, cv.height); };
      im.src = img;
    }
  }
  scheduleRowStatus(row);
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
  state.statusTimer = setTimeout(() => fetchRowStatus(row), 150);
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
    showRow(state.nRows - 1);   // stopPlay aborts the in-flight final batch; ensure it renders
  }
}

function stopPlay() {
  if (state.playTimer) { clearInterval(state.playTimer); state.playTimer = null; }
  state.playing = false;
  // cancel the in-flight frame batch so no image is drawn after the stop
  if (state.frameAbort) { state.frameAbort.abort(); state.frameAbort = null; }
  const b = $('btn-play');
  if (b) b.innerHTML = ICONS.play;
}

function togglePlay() {
  if (state.playing) {
    const snapRow = state.row;
    stopPlay();
    // the rendered picture lags the row counter by the fetch latency; snap
    // the paused frame to the exact row so video and counter agree instantly
    if (state.nRows > 0) showRow(snapRow);
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
