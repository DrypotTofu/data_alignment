/* VLGP Web platform v0 skeleton — session browse + 3-view frames +
 * IN/OUT trim + status + save (T10 migration baseline). */
'use strict';

const CAMERAS = ['camera_arm_2', 'camera_arm_1', 'camera_arm_3'];
const PREFETCH = 12;          // frames decoded ahead/behind per camera
const MOTION_POINTS = 2000;   // motion-curve pixel budget (server downsample)
const MAX_W = { camera_arm_2: 640, camera_arm_1: 320, camera_arm_3: 320 };
const CURVE_COLORS = { arm1: '#ffb43c', arm2: '#c8c8c8', arm3: '#78dc78', arm4: '#c8c8c8' };

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
  motionDpos: {},             // arm -> Float array (downsampled)
};

const $ = (id) => document.getElementById(id);

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
    li.innerHTML = `${s.name}<span class="sub">${s.n_rows} 行 · ${ann.status || 'pending'}` +
      (ann.trim ? ` · [${ann.trim.start_row}, ${ann.trim.end_row}]` : '') + '</span>';
    li.onclick = () => selectSession(s.name);
    ul.appendChild(li);
  }
}

async function selectSession(name) {
  state.session = name;
  state.row = 0;
  state.playing = false;
  stopPlay();
  try {
    const info = await api(`/api/sessions/${name}`);
    if (!info.n_rows) throw new Error('该会话无对齐表 (未运行 align_camera_anchor)');
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
    await loadMotion();
    updateStatusLabel();
    updateTrimInfo();
    await showRow(0);
  } catch (err) {
    alert('无法打开会话: ' + err.message);
    state.session = null;
    state.nRows = 0;
    $('session-name').textContent = name + ' (不可用)';
    $('btn-in').disabled = $('btn-out').disabled = $('btn-save').disabled = true;
    $('status').disabled = true;
  }
}

async function loadMotion() {
  const m = await api(`/api/sessions/${state.session}/alignment?points=${MOTION_POINTS}`);
  state.motionRows = m.rows;
  state.motionDpos = m.dpos || {};
  drawMotion();
}

function drawMotion() {
  const cv = $('motion'), ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  const rows = state.motionRows;
  if (!rows.length) return;
  const arms = Object.keys(state.motionDpos);
  // PyQt5 parity: each series normalised to its own max.
  arms.forEach((arm) => {
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
  });
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
  // legend
  const legend = [
    ['#ffb43c', 'arm1 dpos'], ['#78dc78', 'arm3 dpos'],
    ['#c8c8c8', 'arm2/4 dpos'], ['#7896ff', 'gripper arm1'],
  ];
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
  const rows = [];
  for (let r = row - PREFETCH; r <= row + PREFETCH; r++) {
    if (r >= 0 && r < state.nRows) rows.push(r);
  }
  const frames = {};
  await Promise.all(CAMERAS.map(async (cam) => {
    const body = { cam, rows, max_width: MAX_W[cam] };
    const res = await api(`/api/sessions/${state.session}/frames/batch`,
                          { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(body) });
    frames[cam] = res.images || {};
  }));
  for (const cam of CAMERAS) {
    const img = frames[cam][String(row)];
    if (img) {
      const cv = $(`cam-${cam.slice(-1)}`);
      const ctx = cv.getContext('2d');
      const im = new Image();
      im.onload = () => { ctx.drawImage(im, 0, 0, cv.width, cv.height); };
      im.src = img;
    }
  }
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
  if (step(stepN)) stopPlay();
}

function stopPlay() {
  clearInterval(state.playTimer);
  state.playTimer = null;
  state.playing = false;
  $('btn-play').textContent = '▶';
}

function togglePlay() {
  if (state.playing) { stopPlay(); return; }
  if (state.row >= state.nRows - 1) state.row = 0;
  state.playing = true;
  state.playClock = performance.now();
  state.playAccum = 0;
  $('btn-play').textContent = '⏸';
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
