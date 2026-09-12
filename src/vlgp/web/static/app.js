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
  pipelineJob: null,          // active pipeline job id (verify/build/video)
  pipelineTimer: null,        // pipeline status poller
  settings: null,             // /api/settings payload (active data root)
  browse: null,               // last /api/fs/list payload (picker state)
  browseTarget: null,         // directory the picker would switch to
  cache: null,                // last /api/settings/cache scan (cache cleanup)
  acqConfigs: null,           // /api/acquisition/configs payload
  acqPreflight: null,         // last preflight result (gates 开始录制)
  acqTimer: null,             // acquisition status poller
  acqRefreshed: false,        // session list refreshed after a recording
};

const $ = (id) => document.getElementById(id);

// HTML-escape untrusted strings before interpolating into innerHTML
// (session names come from the filesystem; never trust them).
function esc(v) {
  return String(v).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

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
  let sessions = [];
  let data = {};
  try {
    data = await api('/api/sessions');
    sessions = data.sessions || [];
  } catch (e) {
    $('session-list').innerHTML = '';
    const li = document.createElement('li');
    li.textContent = '读取会话失败: ' + e.message;
    $('session-list').appendChild(li);
    return;
  }
  const ul = $('session-list');
  ul.innerHTML = '';
  if (!sessions.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = '该数据目录下没有已对齐的会话';
    ul.appendChild(li);
  }
  for (const s of sessions) {
    const li = document.createElement('li');
    const ann = s.annotations || {};
    li.appendChild(document.createTextNode(s.name));          // no innerHTML
    if (!s.has_alignment) {
      const badge = document.createElement('span');
      badge.className = 'badge';
      badge.textContent = '未对齐';
      li.appendChild(badge);
    }
    const sub = document.createElement('span');
    sub.className = 'sub';
    let t = `${s.n_rows} 行 · ${ann.status || 'pending'}`;
    if (ann.trim) t += ` · [${ann.trim.start_row}, ${ann.trim.end_row}]`;
    sub.textContent = t;
    li.appendChild(sub);
    li.onclick = () => selectSession(s.name);
    ul.appendChild(li);
  }
}

// ------------------------------------------------------- data root picker
// The browser cannot open a folder dialog on the server, so the picker is
// built from /api/fs/* (drives + directory listing + capacity) and the active
// root is switched through PUT /api/settings/root (persisted server-side).
function fmtGb(v) { return (v === null || v === undefined) ? '?' : `${v} GB`; }

function renderRootBar() {
  const s = state.settings;
  const cur = $('root-current');
  const disk = $('root-disk');
  if (!s) {
    cur.textContent = '未读取数据目录…';
    disk.textContent = '';
    return;
  }
  cur.textContent = s.root;
  cur.title = s.root;
  const d = s.disk;
  let t = `${s.n_sessions} 个会话`;
  if (d) t += ` · 剩余 ${fmtGb(d.free_gb)} / ${fmtGb(d.total_gb)} (${d.free_pct}%)`;
  if (!s.writable) t += ' · 只读';
  disk.textContent = t;
  disk.classList.toggle('warn', !!d && d.free_pct < 10);
}

async function loadSettings() {
  try {
    state.settings = await api('/api/settings');
  } catch { state.settings = null; }
  renderRootBar();
}

function setDialogMsg(text, kind) {
  const el = $('root-dialog-msg');
  el.textContent = text || '';
  el.className = 'dialog-msg' + (kind ? ' ' + kind : '');
}

function renderDrives(drives) {
  const box = $('root-drives');
  box.innerHTML = '';
  for (const d of drives) {
    const b = document.createElement('button');
    b.className = 'chip';
    const free = d.disk ? d.disk.free_gb : null;
    if (d.disk && d.disk.free_pct < 10) b.classList.add('low');
    b.innerHTML = `${esc(d.label)}<span class="free">${free === null ? '' :
      '剩余 ' + esc(String(free)) + ' GB'}</span>`;
    b.onclick = () => browseTo(d.path);
    box.appendChild(b);
  }
}

function renderRecent(roots) {
  const box = $('root-recent');
  box.innerHTML = '';
  if (!roots || !roots.length) {
    box.textContent = '（暂无）';
    return;
  }
  for (const r of roots) {
    const b = document.createElement('button');
    b.className = 'chip';
    b.textContent = r;
    b.title = r;
    b.onclick = () => browseTo(r);
    box.appendChild(b);
  }
}

function renderAcq(info) {
  if (!info) return;
  $('acq-dir-input').value = info.base_dir || '';
  const cfg = (info.configs || []).map((c) =>
    `${c.path}${c.base_dir ? ' = ' + c.base_dir : '（无 base_dir）'}`).join('　|　');
  const warn = info.in_sync ? '' : '  ⚠ 两个采集配置的 base_dir 不一致';
  $('acq-hint').textContent = cfg + warn;
}

function renderEntries(data) {
  const ul = $('root-entries');
  ul.innerHTML = '';
  const entries = data.entries || [];
  if (!entries.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = '（没有子文件夹）';
    ul.appendChild(li);
  }
  for (const e of entries) {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.className = 'ename';
    name.textContent = e.name;
    li.appendChild(name);
    const mark = document.createElement('span');
    mark.className = 'emark';
    if (e.is_session) mark.textContent = '会话';
    else if (e.n_sessions) mark.textContent = `${e.n_sessions} 个会话`;
    li.appendChild(mark);
    const pick = document.createElement('button');
    pick.className = 'pick';
    pick.textContent = '选择此项';
    pick.onclick = (ev) => {
      ev.stopPropagation();
      state.browseTarget = e.path;
      renderEntries(data);
      $('root-path-input').value = e.path;
    };
    li.appendChild(pick);
    li.onclick = () => browseTo(e.path);
    li.classList.toggle('sel', state.browseTarget === e.path);
    ul.appendChild(li);
  }
}

async function browseTo(path) {
  setDialogMsg('读取中…');
  try {
    const data = await api('/api/fs/list?path=' + encodeURIComponent(path));
    state.browse = data;
    state.browseTarget = data.path;
    $('root-path-input').value = data.path;
    $('root-up').disabled = !data.parent;
    const d = data.disk;
    let stat = `${data.path}　·　${data.n_sessions === null ? '会话数未统计（目录过大）' :
      data.n_sessions + ' 个会话'}`;
    if (d) stat += `　·　剩余 ${fmtGb(d.free_gb)} / ${fmtGb(d.total_gb)} (${d.free_pct}%)`;
    if (!data.writable) stat += '　·　只读（缓存与标注将回退到仓库 var/ 或无法保存）';
    $('root-browse-stat').textContent = stat;
    renderEntries(data);
    setDialogMsg('');
  } catch (e) {
    setDialogMsg('无法读取: ' + e.message, 'err');
  }
}

async function openRootPicker() {
  $('root-dialog').hidden = false;
  setDialogMsg('');
  if (!state.settings) await loadSettings();
  const s = state.settings;
  renderRecent(s ? s.recent_roots : []);
  renderAcq(s ? s.acquisition : null);
  state.cache = null;
  $('cache-stat').textContent = '尚未扫描';
  $('cache-hint').textContent = '';
  $('cache-clean').disabled = true;
  $('acq-save').disabled = false;
  try {
    const { drives } = await api('/api/fs/drives');
    renderDrives(drives);
  } catch { /* leave the drive chips empty */ }
  await browseTo(s ? s.root : $('root-path-input').value);
}

function closeRootPicker() { $('root-dialog').hidden = true; }

async function switchRoot(path) {
  if (!path) return;
  setDialogMsg('切换中…');
  $('root-use').disabled = true;
  try {
    const r = await fetch('/api/settings/root', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path,
        sync_acquisition: $('root-sync-acq').checked,
      }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = body.detail || body;
      setDialogMsg((d && (d.message || d.error)) || `切换失败 (${r.status})`, 'err');
      return;
    }
    state.settings = body;
    renderRootBar();
    renderRecent(body.recent_roots);
    renderAcq(body.acquisition);
    const acq = body.acquisition_applied;
    if (acq && acq.error) setDialogMsg('数据目录已切换，但采集目录未更新: ' + acq.error, 'err');
    closeRootPicker();
    resetSessionView();          // stale frames belong to the previous root
    await loadSessions();
  } catch (e) {
    setDialogMsg('切换失败: ' + e.message, 'err');
  } finally {
    $('root-use').disabled = false;
  }
}

async function saveAcqDir() {
  const base_dir = $('acq-dir-input').value.trim();
  if (!base_dir) { setDialogMsg('采集输出目录不能为空', 'err'); return; }
  setDialogMsg('保存中…');
  try {
    const r = await fetch('/api/settings/acquisition', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_dir, sync_configs: true }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = body.detail || body;
      setDialogMsg((d && d.error) || `保存失败 (${r.status})`, 'err');
      return;
    }
    renderAcq(body.acquisition);
    setDialogMsg(`已写入 ${body.base_dir}${body.created ? '（目录已创建）' : ''}`, 'ok');
  } catch (e) {
    setDialogMsg('保存失败: ' + e.message, 'err');
  }
}

// =========================================================== acquisition UI
// Camera binding (grab a frame per index to identify the device) + recording
// control. The server refuses camera access while a recording runs, so the
// buttons follow the job state.
const ACQ_SCAN_INDEXES = 8;

function setAcqMsg(text, kind) {
  const el = $('acq-msg');
  el.textContent = text || '';
  el.className = 'dialog-msg' + (kind ? ' ' + kind : '');
}

async function acqJson(path, opts) {
  const r = await fetch(path, opts);
  let body = {};
  try { body = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    const d = body.detail || body;
    const msg = (d && (d.message || d.error)) || (typeof d === 'string' ? d : '')
      || `${r.status} ${r.statusText}`;
    const err = new Error(msg);
    err.status = r.status;
    err.body = d;
    throw err;
  }
  return body;
}

async function openAcqDialog() {
  $('acq-dialog').hidden = false;
  setAcqMsg('');
  $('acq-log').hidden = true;
  await loadAcqConfigs();
  await pollAcq();
}

function closeAcqDialog() {
  $('acq-dialog').hidden = true;
  clearTimeout(state.acqTimer);
  state.acqTimer = null;
}

async function loadAcqConfigs() {
  try {
    const data = await acqJson('/api/acquisition/configs');
    state.acqConfigs = data.configs || [];
    const sel = $('acq-config');
    const keep = sel.value;
    sel.innerHTML = '';
    for (const c of state.acqConfigs) {
      const o = document.createElement('option');
      o.value = c.path;
      o.textContent = `${c.name}${c.exists ? '' : '（缺失）'}`;
      sel.appendChild(o);
    }
    if (keep && state.acqConfigs.some((c) => c.path === keep)) sel.value = keep;
    if (!$('acq-name').value) $('acq-name').value = data.suggested_name || '';
    renderAcqCameras();
    renderAcqTarget();
    setAcqState(data.status);
  } catch (e) {
    setAcqMsg('读取采集配置失败: ' + e.message, 'err');
  }
}

function currentAcqConfig() {
  const p = $('acq-config').value;
  return (state.acqConfigs || []).find((c) => c.path === p) || null;
}

function renderAcqTarget() {
  const c = currentAcqConfig();
  const base = c && c.base_dir ? c.base_dir : '(未设置 base_dir)';
  const name = $('acq-name').value.trim() || '(未命名)';
  $('acq-target').textContent =
    `输出目录: ${base}　|　会话: ${name}　|　对齐表在录制后生成`;
}

function renderAcqCameras() {
  const c = currentAcqConfig();
  const t = $('acq-bind-table');
  t.innerHTML = '';
  if (!c) return;
  if (c.errors && c.errors.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 5;
    td.className = 'err';
    td.textContent = '配置校验失败: ' + c.errors.join(' / ');
    tr.appendChild(td);
    t.appendChild(tr);
    return;
  }
  const head = document.createElement('tr');
  for (const h of ['相机', 'arm', 'index', '分辨率/FPS', '预览']) {
    const th = document.createElement('th');
    th.textContent = h;
    head.appendChild(th);
  }
  t.appendChild(head);

  for (const cam of c.cameras || []) {
    const tr = document.createElement('tr');
    if (!cam.enabled) tr.className = 'off';

    const name = document.createElement('td');
    name.textContent = cam.name + (cam.enabled ? '' : '（已禁用）');
    tr.appendChild(name);

    const arm = document.createElement('td');
    arm.textContent = cam.arm_id === null || cam.arm_id === undefined
      ? '—' : `arm${cam.arm_id}`;
    tr.appendChild(arm);

    const idx = document.createElement('td');
    const sel = document.createElement('select');
    sel.dataset.name = cam.name;
    for (let i = 0; i <= ACQ_SCAN_INDEXES; i++) {
      const o = document.createElement('option');
      o.value = String(i);
      o.textContent = String(i);
      sel.appendChild(o);
    }
    sel.value = String(cam.camera_index ?? 0);
    idx.appendChild(sel);
    tr.appendChild(idx);

    const res = document.createElement('td');
    res.textContent = `${cam.width || '?'}x${cam.height || '?'}`
      + `${cam.target_fps ? ' @' + cam.target_fps : (cam.fps ? ' @' + cam.fps : '')}`;
    tr.appendChild(res);

    const pv = document.createElement('td');
    const box = document.createElement('div');
    box.className = 'sub';
    box.dataset.preview = cam.name;
    box.textContent = '—';
    const btn = document.createElement('button');
    btn.className = 'mini';
    btn.textContent = '预览';
    btn.onclick = () => previewCamera(cam.name, sel.value, box);
    pv.appendChild(btn);
    pv.appendChild(box);
    tr.appendChild(pv);

    t.appendChild(tr);
  }
}

async function previewCamera(name, index, box) {
  box.textContent = '抓帧中…';
  try {
    const url = `/api/acquisition/cameras/preview?index=${encodeURIComponent(index)}`
      + '&width=640&height=480';
    const r = await fetch(url);
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      const d = body.detail || body;
      box.textContent = '';
      box.className = 'err';
      box.textContent = (d && d.error) || `失败 (${r.status})`;
      return;
    }
    const blob = await r.blob();
    const img = document.createElement('img');
    img.src = URL.createObjectURL(blob);
    img.title = `index ${index} · ${r.headers.get('X-Camera-Size') || ''}`;
    const meta = document.createElement('div');
    meta.textContent = `index ${index} ${r.headers.get('X-Camera-Size') || ''}`;
    box.textContent = '';
    box.className = 'sub';
    box.appendChild(img);
    box.appendChild(meta);
  } catch (e) {
    box.className = 'err';
    box.textContent = '失败: ' + e.message;
  }
}

async function scanCameras() {
  const out = $('acq-scan-result');
  out.textContent = '扫描中…（逐个打开 index 0..8，约需数秒）';
  try {
    const data = await acqJson('/api/acquisition/cameras/scan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_index: ACQ_SCAN_INDEXES }),
    });
    const parts = (data.entries || []).map((e) => (e.opened
      ? `index ${e.index}: ${e.width}x${e.height} @${e.fps}`
        + (e.frame_read ? '' : '（取不到帧）')
      : `index ${e.index}: 无`));
    out.textContent = `找到可用: ${(data.found || []).join(', ') || '无'}`
      + `（${data.elapsed_s}s）　` + parts.join('　|　');
  } catch (e) {
    out.textContent = '扫描失败: ' + e.message;
  }
}

async function saveBindings() {
  const cfg = currentAcqConfig();
  if (!cfg) return;
  const bindings = {};
  document.querySelectorAll('#acq-bind-table select[data-name]').forEach((sel) => {
    const cam = (cfg.cameras || []).find((c) => c.name === sel.dataset.name);
    if (cam && cam.arm_id) bindings[`arm_${cam.arm_id}`] = parseInt(sel.value, 10);
  });
  setAcqMsg('保存绑定…');
  try {
    const out = await acqJson('/api/acquisition/bindings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: cfg.path, bindings, sync_configs: true }),
    });
    setAcqMsg(`绑定已写入: ${JSON.stringify(out.bindings)}`, 'ok');
    await loadAcqConfigs();
  } catch (e) {
    setAcqMsg('保存失败: ' + e.message, 'err');
  }
}

async function runPreflight() {
  const cfg = currentAcqConfig();
  if (!cfg) return;
  const ul = $('acq-checks');
  ul.innerHTML = '';
  setAcqMsg('自检中…（相机逐台抓帧 + ADS 连接 + 磁盘预估）');
  try {
    const data = await acqJson('/api/acquisition/preflight', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        config: cfg.path,
        reserve_minutes: parseFloat($('acq-reserve').value) || 20,
        session_name: $('acq-name').value.trim() || null,
      }),
    });
    for (const c of data.checks || []) {
      const li = document.createElement('li');
      const tag = document.createElement('span');
      tag.className = 'tag ' + (c.status === 'ok' ? 'ok'
        : c.status === 'fail' ? 'fail' : 'warn');
      tag.textContent = c.status === 'ok' ? '通过' : c.status === 'fail' ? '失败' : '提示';
      const detail = document.createElement('span');
      detail.className = 'detail';
      detail.textContent = ` ${c.label}: ${c.detail}`;
      li.appendChild(tag);
      li.appendChild(detail);
      ul.appendChild(li);
    }
    state.acqPreflight = data;
    setAcqMsg(data.ok
      ? `自检通过（按 ${data.estimate.reserve_minutes} 分钟预留 `
        + `${data.estimate.reserve_gb} GB）`
      : `自检未通过: ${(data.failed || []).join(', ')} —— 仍可强制开始，但风险自负`,
      data.ok ? 'ok' : 'err');
  } catch (e) {
    setAcqMsg('自检失败: ' + e.message, 'err');
  }
}

async function startRecording() {
  const cfg = currentAcqConfig();
  if (!cfg) return;
  const pf = state.acqPreflight;
  if (!pf || pf.config !== cfg.path || !pf.ok) {
    const warn = pf && pf.failed && pf.failed.length
      ? `自检未通过（${pf.failed.join(', ')}）` : '尚未通过自检';
    if (!window.confirm(`${warn}。\n仍要开始录制吗？（可能导致录制中断或磁盘写满）`)) return;
  }
  const name = $('acq-name').value.trim();
  setAcqMsg('启动采集进程…');
  try {
    const out = await acqJson('/api/acquisition/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: cfg.path, name }),
    });
    if (out.busy) { setAcqMsg('已有录制在进行中', 'err'); return; }
    setAcqMsg('已启动，等待录制开始…', 'ok');
    $('acq-log').hidden = false;
    pollAcq(true);
  } catch (e) {
    setAcqMsg('启动失败: ' + e.message, 'err');
  }
}

async function stopRecording() {
  if (!window.confirm('确定停止录制？（刷盘并写 session.json，需要几秒）')) return;
  setAcqMsg('正在停止…');
  try {
    const out = await acqJson('/api/acquisition/stop', { method: 'POST' });
    if (out.still_running) {
      setAcqMsg(`仍在停止中（已等待 ${out.waited_s}s）`, 'err');
    } else {
      setAcqMsg('已停止', 'ok');
    }
    pollAcq(true);
  } catch (e) {
    setAcqMsg('停止失败: ' + e.message, 'err');
  }
}

async function killRecording() {
  if (!window.confirm('强制结束采集进程？\n\nsession.json 可能缺失、CSV/图片流尾部可能'
    + '不完整。只有在“停止录制”长时间无响应时才使用。')) return;
  try {
    const out = await acqJson('/api/acquisition/kill', { method: 'POST' });
    setAcqMsg(`已强制结束（exit ${out.exit_code}）`, 'err');
    pollAcq(true);
  } catch (e) {
    setAcqMsg('强制结束失败: ' + e.message, 'err');
  }
}

function setAcqState(st) {
  const el = $('acq-state');
  const recording = st && st.recording;
  el.textContent = !st || st.state === 'idle' ? '空闲'
    : st.state === 'starting' ? '准备中'
      : st.state === 'recording' ? '● 录制中' : st.state === 'stopping' ? '停止中'
        : st.state === 'done' ? '已结束' : '出错';
  el.className = 'acq-state' + (recording ? ' rec'
    : st && st.state === 'done' ? ' ok' : '');
  $('acq-start').disabled = recording || (st && st.state === 'starting');
  $('acq-stop').disabled = !recording;
  $('acq-kill').disabled = !recording && !(st && st.state === 'stopping');
  $('acq-scan').disabled = !!recording;
}

async function pollAcq(immediate) {
  clearTimeout(state.acqTimer);
  state.acqTimer = null;
  if ($('acq-dialog').hidden) return;
  try {
    const st = await acqJson('/api/acquisition/status');
    setAcqState(st);
    if (st.state === 'idle') {
      $('acq-live').textContent = '未开始';
    } else {
      // small amounts in MB — "已写 0 GB" while 30 MB is on disk reads as broken
      const written = st.bytes_written >= 1073741824
        ? `${st.gb_written} GB` : `${st.mb_written} MB`;
      const cams = st.camera_frames && Object.keys(st.camera_frames).length
        ? ' · 帧 ' + Object.entries(st.camera_frames)
          .map(([k, v]) => `${k.replace('camera_arm_', 'arm')}=${v}`).join(' ')
        : '';
      $('acq-live').textContent =
        `${st.state} · ${st.elapsed_s}s · 已写 ${written}`
        + (st.mb_per_s !== null && st.mb_per_s !== undefined
          ? ` · ${st.mb_per_s} MB/s (${st.gb_per_min} GB/分钟)` : '')
        + (st.state === 'starting' ? ' · 正在打开相机…' : '')
        + cams
        + (st.stdin_failsafe ? '' : ' · [无 stdin 兜底]');
      const warn = $('acq-warn');
      if (st.warnings && st.warnings.length) {
        warn.hidden = false;
        warn.textContent = '⚠ ' + st.warnings.join('  |  ');
      } else {
        warn.hidden = true;
        warn.textContent = '';
      }
      if (st.error) {
        warn.hidden = false;
        warn.textContent = '⚠ ' + st.error;
      }
      if (!$('acq-log').hidden) {
        const r = await fetch('/api/acquisition/log');
        if (r.ok) $('acq-log').textContent = await r.text();
      }
      if (st.state !== 'recording' && st.state !== 'starting'
          && st.state !== 'stopping') {
        $('acq-stop').disabled = true;
        $('acq-kill').disabled = true;
        if (!state.acqRefreshed) {          // pick up the new session once
          state.acqRefreshed = true;
          loadSessions();
          if (st.session) {
            setAcqMsg(`录制结束：${st.session}（${st.gb_written} GB）`
              + ' —— 会话列表已刷新，点开它会自动生成对齐表', 'ok');
          }
        }
      }
    }
    state.acqTimer = setTimeout(() => pollAcq(), 1500);
  } catch {
    state.acqTimer = setTimeout(() => pollAcq(), 3000);
  }
}

async function revealCurrentDir() {
  const p = (state.browse && state.browse.path) || $('root-path-input').value.trim();
  if (!p) return;
  try {
    const r = await fetch('/api/fs/reveal', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: p }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      const d = body.detail || body;
      setDialogMsg((d && d.error) || `打开失败 (${r.status})`, 'err');
    }
  } catch (e) {
    setDialogMsg('打开失败: ' + e.message, 'err');
  }
}

// Preview caches of deleted sessions are the main way the platform wastes
// disk (a full 10-min session preload leaves ~0.9 GB behind).
async function scanCache() {
  $('cache-stat').textContent = '扫描中…（缓存文件很多时需数秒）';
  $('cache-clean').disabled = true;
  try {
    const r = await fetch('/api/settings/cache');
    const body = await r.json();
    if (!r.ok) { $('cache-stat').textContent = '扫描失败'; return; }
    state.cache = body;
    $('cache-stat').textContent =
      `共 ${body.total_mb} MB / ${(body.dirs || []).length} 个会话；`
      + `其中失效 ${body.stale_mb} MB (${body.stale_count} 个)`;
    $('cache-hint').textContent = (body.cache_roots || []).join('　|　');
    $('cache-clean').disabled = !body.stale_count;
  } catch (e) {
    $('cache-stat').textContent = '扫描失败: ' + e.message;
  }
}

async function cleanCache() {
  const c = state.cache;
  const names = (c && c.dirs || []).filter((d) => d.stale).map((d) => d.session);
  if (!names.length) return;
  const files = (c.dirs || []).filter((d) => d.stale)
    .reduce((n, d) => n + (d.files || 0), 0);
  // deleting ~370k small JPEGs measured 286 s — say so before starting
  const msg = `将删除 ${names.length} 个已不存在会话的预览缓存`
    + `（约 ${c.stale_mb} MB / ${files} 个文件）：\n`
    + names.join(', ')
    + `\n\n缓存可以重新生成（重新打开会话时自动重建）。\n`
    + `文件很多时删除需要数分钟，期间页面会一直显示"删除中"，请不要重复点击。\n\n确认删除？`;
  if (!window.confirm(msg)) return;
  $('cache-stat').textContent = `删除中…（${files} 个文件，可能需要数分钟）`;
  $('cache-clean').disabled = true;
  try {
    const r = await fetch('/api/settings/cache/clean', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true }),
    });
    const body = await r.json();
    if (!r.ok) {
      const d = body.detail || body;
      $('cache-stat').textContent = '删除失败: ' + ((d && d.error) || r.status);
      return;
    }
    state.cache = null;
    $('cache-clean').disabled = true;
    $('cache-hint').textContent = '';
    $('cache-stat').textContent =
      `已删除 ${body.removed_count} 个缓存目录，释放 ${body.freed_mb} MB`
      + (body.failed && body.failed.length ? `；${body.failed.length} 个失败` : '');
  } catch (e) {
    $('cache-stat').textContent = '删除失败: ' + e.message;
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

// Drop every decoded frame + in-flight preload for the current view. Called on
// session switch AND on data-root switch (same session name can resolve to
// different files on another root, so nothing may be reused).
function clearFrameCache() {
  state.preloadGen += 1;      // late responses write into the discarded Maps
  state.preloadTimer = null;
  state.preloadBusy = false;
  for (const cam of Object.keys(state.frameCache)) {
    const c = state.frameCache[cam];
    if (!c) continue;
    for (const im of c.values()) {
      if (im && im._url) URL.revokeObjectURL(im._url);
    }
  }
  state.frameCache = {};
  state.crit = {};
  state.lastDraw = {};
  state.preloadPct = -1;
  state.serverPreload = null;
  clearTimeout(state.preloadPollTimer);
  $('preload-bar').hidden = true;
}

// Back to the "nothing selected" state (data-root switch, or a session that
// could not be opened) — clears the canvases and disables the controls.
function resetSessionView() {
  stopPlay();
  clearFrameCache();
  resetPipeline();
  clearTimeout(state.statusTimer);
  state.session = null;
  state.nRows = 0;
  state.row = 0;
  state.annotations = null;
  state.trim = null;
  state.status = 'pending';
  state.rowStatus = null;
  state.motionRows = [];
  state.motionDpos = {};
  state.motionGripper = {};
  for (const cam of CAMERAS) {
    const cv = $(`cam-${cam.slice(-1)}`);
    if (cv) cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
  }
  $('session-name').textContent = '未选择会话';
  $('row-info').textContent = '';
  $('status-label').textContent = '';
  $('curve-bar').hidden = true;
  $('slider').max = 0;
  $('slider').value = 0;
  $('time-info').textContent = '';
  $('trim-info').textContent = '';
  $('btn-in').disabled = $('btn-out').disabled = true;
  $('btn-save').disabled = $('btn-next-session').disabled = true;
  $('status').disabled = true;
  $('arm-tabs').innerHTML = '';
  $('arm-status').innerHTML = '';
  setPipelineEnabled(false);
  document.querySelectorAll('#session-list li').forEach(
    (li) => li.classList.remove('active'));
}

async function selectSession(name) {
  state.session = name;
  state.row = 0;
  state.playing = false;
  stopPlay();
  // invalidate the previous session's preload/cache before loading the new one
  clearFrameCache();
  resetPipeline();
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
    setPipelineEnabled(true);
    await loadMotion();
    updateStatusLabel();
    updateTrimInfo();
    buildArmTabs();
    await showRow(0);
    startServerPreload(name);                          // whole-session preload
    loadSessions();                                    // refresh badge
  } catch (err) {
    alert('无法打开会话: ' + err.message);
    resetSessionView();
    $('session-name').textContent = name + ' (不可用)';
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

// ------------------------------------------------------------------ pipeline
// Run the CLI pipeline steps (verify_alignment / build_srth_dataset /
// images_to_video) as server-side background jobs, poll status, show the log.
function setPipelineEnabled(on) {
  for (const id of ['btn-verify', 'btn-build', 'btn-video', 'btn-meta']) {
    $(id).disabled = !on;
  }
}

function resetPipeline() {
  clearTimeout(state.pipelineTimer);
  state.pipelineTimer = null;
  state.pipelineJob = null;
  $('pipe-status').textContent = '';
  $('pipe-log').hidden = true;
}

function startPipeline(kind, label) {
  if (!state.session) return;
  if (state.pipelineJob) { alert('已有流水线任务在运行，请等待完成'); return; }
  const st = $('pipe-status');
  st.textContent = `${label}启动中…`;
  $('pipe-log').hidden = true;
  (async () => {
    try {
      let url = `/api/sessions/${state.session}/pipeline/${kind}`;
      let r = await fetch(url, { method: 'POST' });
      if (r.status === 409) {
        // video export without IN/OUT: ask before exporting the whole video
        let body = {};
        try { body = await r.json(); } catch { /* ignore */ }
        const detail = body.detail || body;
        if (detail && detail.warning) {
          if (!window.confirm(detail.message || '未设置 IN/OUT 裁剪点，确认导出全部预览视频？')) {
            st.textContent = '已取消导出';
            return;
          }
          r = await fetch(url + '?confirm=1', { method: 'POST' });
        }
      }
      if (!r.ok) {
        let body = {};
        try { body = await r.json(); } catch { /* ignore */ }
        st.textContent = `启动失败: ${JSON.stringify(body.detail || body)}`;
        return;
      }
      const job = await r.json();
      state.pipelineJob = job.id;
      pollPipeline(job.id);
    } catch (e) { st.textContent = '启动失败: ' + e.message; }
  })();
}

function pollPipeline(jobId) {
  clearTimeout(state.pipelineTimer);
  state.pipelineTimer = setTimeout(async () => {
    if (state.pipelineJob !== jobId) return;
    try {
      const st = await api(`/api/pipeline/${jobId}/status`);
      const el = $('pipe-status');
      if (st.status === 'running') {
        el.textContent = `${st.label}运行中… (${Math.round((Date.now() / 1000) - st.started_at)}s)`;
        pollPipeline(jobId);
      } else {
        el.textContent = st.status === 'done'
          ? `${st.label}完成 (${st.elapsed_s}s)`
          : `${st.label}失败 (exit ${st.exit_code})`;
        state.pipelineJob = null;
        showPipeLog(jobId, true);          // auto-show the log on completion
        if (st.status === 'done') loadSessions();  // build/video change session state
      }
    } catch { pollPipeline(jobId); }       // transient error, keep polling
  }, 800);
}

async function showPipeLog(jobId, force) {
  try {
    const r = await fetch(`/api/pipeline/${jobId}/log`);
    if (!r.ok) return;
    const text = await r.text();
    const pre = $('pipe-log');
    pre.textContent = text;
    if (force || state.pipelineJob === jobId) pre.hidden = false;
  } catch { /* ignore */ }
}

// ------------------------------------------------------- session meta (T8)
async function openMetaForm() {
  const f = $('meta-form');
  if (f.hidden === false) { f.hidden = true; return; }   // toggle closed
  try {
    const meta = await api(`/api/sessions/${state.session}/meta`);
    $('meta-task-instruction').value = meta.task_instruction || '';
    $('meta-task-type').value = meta.task_type || 'other';
    $('meta-operator').value = meta.operator || '';
    $('meta-organ-model').value = meta.organ_model || '';
    $('meta-calibration').value = meta.camera_calibration || '';
    $('meta-notes').value = meta.notes || '';
    const instr = meta.instruments || {};
    $('meta-instruments').value =
      Object.entries(instr).map(([k, v]) => `${k}=${v}`).join(',');
    f.hidden = false;
  } catch (e) { alert('读取元数据失败: ' + e.message); }
}

async function saveMeta() {
  const body = {
    task_instruction: $('meta-task-instruction').value,
    task_type: $('meta-task-type').value,
    operator: $('meta-operator').value,
    organ_model: $('meta-organ-model').value,
    camera_calibration: $('meta-calibration').value,
    notes: $('meta-notes').value,
  };
  const instrStr = $('meta-instruments').value.trim();
  const instr = {};
  if (instrStr) {
    for (const pair of instrStr.split(',')) {
      if (pair.includes('=')) {
        const [k, v] = pair.split('=', 2);
        instr[k.trim()] = v.trim();
      }
    }
  }
  body.instruments = instr;
  try {
    const r = await fetch(`/api/sessions/${state.session}/meta`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    if (!r.ok) {
      let e = {};
      try { e = await r.json(); } catch { /* ignore */ }
      alert('保存失败: ' + JSON.stringify(e.detail || e));
      return;
    }
    alert('已保存 session_meta.json');
    $('meta-form').hidden = true;
  } catch (e) { alert('保存失败: ' + e.message); }
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
  } else if (sp && sp.status === 'error') {
    pct = Math.round(preloadProgress() * 100);
    label = `整段预加载失败（改用窗口预加载）`;
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
    `<span class="k">${esc(k)}</span><span class="v${v === '—' ? ' na' : ''}">${esc(v)}</span>`
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
// pipeline
$('btn-verify').onclick = () => startPipeline('verify', '验证对齐');
$('btn-build').onclick = () => startPipeline('build', '构建数据集');
$('btn-video').onclick = () => startPipeline('video', '导出预览视频');
$('btn-meta').onclick = openMetaForm;
// data-root picker
$('btn-root').onclick = openRootPicker;
$('root-close').onclick = closeRootPicker;
$('root-use').onclick = () => switchRoot(state.browseTarget ||
  $('root-path-input').value.trim());
$('root-go').onclick = () => browseTo($('root-path-input').value.trim());
$('root-up').onclick = () => {
  if (state.browse && state.browse.parent) browseTo(state.browse.parent);
};
$('root-reveal').onclick = revealCurrentDir;
$('cache-scan').onclick = scanCache;
$('cache-clean').onclick = cleanCache;
$('root-path-input').onkeydown = (e) => {
  if (e.key === 'Enter') browseTo($('root-path-input').value.trim());
};
$('acq-save').onclick = saveAcqDir;
$('acq-use-root').onclick = () => {
  const p = (state.browse && state.browse.path) || $('root-path-input').value.trim();
  if (p) $('acq-dir-input').value = p;
};
$('root-dialog').onclick = (e) => {                 // click the backdrop closes
  if (e.target === $('root-dialog')) closeRootPicker();
};
// acquisition panel
$('btn-acq').onclick = openAcqDialog;
$('acq-close').onclick = closeAcqDialog;
$('acq-dialog').onclick = (e) => {
  if (e.target === $('acq-dialog')) closeAcqDialog();
};
$('acq-config').onchange = () => { renderAcqCameras(); renderAcqTarget(); };
$('acq-name').oninput = renderAcqTarget;
$('acq-name-gen').onclick = async () => {
  try {
    const d = await acqJson('/api/acquisition/configs');
    $('acq-name').value = d.suggested_name || '';
    renderAcqTarget();
  } catch (e) { setAcqMsg('生成会话名失败: ' + e.message, 'err'); }
};
$('acq-scan').onclick = scanCameras;
$('acq-bind-save').onclick = saveBindings;
$('acq-preflight').onclick = runPreflight;
$('acq-start').onclick = startRecording;
$('acq-stop').onclick = stopRecording;
$('acq-kill').onclick = killRecording;
$('acq-log-btn').onclick = () => {
  const pre = $('acq-log');
  pre.hidden = !pre.hidden;
  if (!pre.hidden) pollAcq(true);
};
$('meta-save').onclick = saveMeta;
$('meta-cancel').onclick = () => { $('meta-form').hidden = true; };
$('btn-pipe-log').onclick = () => {
  if (!state.pipelineJob) return;
  showPipeLog(state.pipelineJob, true);
};
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
  if (e.key === 'Escape') {
    if (!$('acq-dialog').hidden) { closeAcqDialog(); return; }
    if (!$('root-dialog').hidden) { closeRootPicker(); return; }
  }
  if (!$('root-dialog').hidden || !$('acq-dialog').hidden) return;  // modal owns keys
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

loadSettings();
loadSessions();
startRenderLoop();
