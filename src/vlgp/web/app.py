"""FastAPI application for the VLGP Web platform (T15 v0 skeleton).

Endpoints
---------
GET  /api/sessions                        list sessions
GET  /api/sessions/{name}                 session info (rows, cameras, annotations)
GET  /api/sessions/{name}/alignment       motion-curve data (?stride=)
GET  /api/sessions/{name}/frames/{cam}/{row}?max_width=   JPEG preview frame
POST /api/sessions/{name}/frames/batch    base64 JPEG map for prefetch windows
GET  /api/sessions/{name}/annotations     read annotations.json
PUT  /api/sessions/{name}/annotations     validate + save annotations.json
GET  /api/task-hierarchy                  T15 controlled vocabulary
GET  /                                    static frontend (index.html)

Run:  python -m vlgp.web.app [--port 8765] [--host 127.0.0.1]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from vlgp.annotations.task_hierarchy import TaskHierarchy
from vlgp.acquisition.config import read_base_dir, set_base_dir
from vlgp.review.write_session_meta import DEFAULT_TEMPLATE, TASK_TYPES
from vlgp.web import fs_browse
from vlgp.web.acquisition_service import _acquisition
from vlgp.web.camera_service import (
    config_cameras,
    preflight,
    preview_jpeg,
    scan_cameras,
    set_bindings,
    suggest_session_name,
)
from vlgp.web.frame_service import (
    FrameService,
    PreloadRegistry,
    clean_cache,
    reset_frame_cache,
    scan_cache,
)
from vlgp.web.pipeline_service import (
    LOG_TAIL_BYTES,
    LOG_TAIL_LINES,
    _pipeline,
    video_trim_args,
)
from vlgp.web.session_index import (
    CAMERA_ORDER,
    NoAlignment,
    SessionIndex,
    SessionNotFound,
)
from vlgp.web.settings import DEFAULT_ROOT, WebSettings

STATIC_DIR = Path(__file__).parent / 'static'
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PORT = 8765
# Acquisition configs offered in the Web「采集」panel. default.yaml is what
# scripts/tools/start_manual_acquisition.bat passes to manual_acquisition.py;
# example_acquisition.yaml is the default --config of scripts/run_acquisition.py;
# arm2_only.yaml is the endoscope-only variant used while the wrist cameras are
# not connected. base_dir / camera_bindings writes are kept in sync across them
# (housekeeping rule: these must not drift apart).
ACQ_CONFIGS = (REPO_ROOT / 'scripts' / 'config' / 'default.yaml',
               REPO_ROOT / 'scripts' / 'config' / 'example_acquisition.yaml',
               REPO_ROOT / 'scripts' / 'config' / 'arm2_only.yaml')
ACQ_LAUNCHER = REPO_ROOT / 'scripts' / 'tools' / 'start_manual_acquisition.bat'
ACQ_STOP_DIR = REPO_ROOT / 'var' / 'web_acq'
# Single-process deployment assumption (CODE_REVIEW_20260830 三.8): the
# preload registry and the JPEG LRU are module-level and only grow; with
# multiple create_app() instances the LRU keys on SessionIndex identity and
# would miss. Fine for the shipped one-server setup.
_PRELOAD_REGISTRY = PreloadRegistry()

VALID_STATUS = {'pending', 'approved', 'rejected'}


class AnnotationsIn(BaseModel):
    version: int = 1
    reviewed_by: str = ''
    reviewed_at: str = ''
    status: str = 'pending'
    reject_reason: str = ''
    trim: dict | None = None
    segments: list = []
    flags: list = []
    notes: str = ''


class BatchIn(BaseModel):
    cam: str
    rows: list[int] = []
    max_width: int = 480

    @field_validator('max_width')
    @classmethod
    def _clamp_max_width(cls, v: int) -> int:
        """Clamp to [32, 1280]: absurd values would upscale instead of
        downscale (resource abuse via a public endpoint)."""
        return min(1280, max(32, int(v)))

    @field_validator('rows')
    @classmethod
    def _limit_rows(cls, v: list[int]) -> list[int]:
        if len(v) > 512:
            raise ValueError('rows: at most 512 per request')
        return v


class RootIn(BaseModel):
    path: str
    sync_acquisition: bool = False


class AcqDirIn(BaseModel):
    base_dir: str
    sync_configs: bool = True


class ScanIn(BaseModel):
    backend: str = 'dshow'
    max_index: int = 8

    @field_validator('max_index')
    @classmethod
    def _clamp(cls, v: int) -> int:
        return min(16, max(0, int(v)))


class BindingsIn(BaseModel):
    config: str
    bindings: dict[str, int]
    sync_configs: bool = True


class PreflightIn(BaseModel):
    config: str
    reserve_minutes: float = 20.0
    session_name: str | None = None
    check_cameras: bool = True
    check_robot: bool = True


class AcqStartIn(BaseModel):
    config: str
    name: str | None = None


def _resolve_config(path: str) -> Path:
    """Acquisition config path, restricted to the repo's scripts/config/."""
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        p = p.resolve()
    except OSError as e:
        raise HTTPException(422, {'error': f'无法解析路径 {path!r}: {e}'})
    allowed = (REPO_ROOT / 'scripts' / 'config').resolve()
    if allowed not in p.parents or p.suffix not in ('.yaml', '.yml'):
        raise HTTPException(422, {
            'error': f'只允许 scripts/config/ 下的 YAML 配置，收到 {p}'})
    if not p.is_file():
        raise HTTPException(404, {'error': f'配置文件不存在: {p}'})
    return p


def create_app(index: SessionIndex | None = None,
               settings: WebSettings | None = None) -> FastAPI:
    settings = settings or WebSettings.load()
    index = index or SessionIndex(settings=settings)
    if not index.root.is_dir():
        # persisted root vanished (unplugged/moved disk): keep serving the
        # repo default instead of starting up empty
        index.set_root(DEFAULT_ROOT)
    frames = FrameService(index)
    hier: TaskHierarchy | None = None
    try:
        hier = TaskHierarchy.load()
    except Exception:
        hier = None

    app = FastAPI(title='VLGP Web Platform', version='0.1.0')
    # Same-origin app (page + API both served from 127.0.0.1:<port>) — CORS is
    # not needed for the app itself; restrict to local origins instead of '*'.
    app.add_middleware(
        CORSMiddleware, allow_origins=[
            'http://127.0.0.1:8765', 'http://localhost:8765',
        ], allow_methods=['*'], allow_headers=['*'])

    # ------------------------------------------------------------- sessions
    @app.get('/api/sessions')
    def list_sessions():
        return {'sessions': index.list_sessions(), 'root': str(index.root)}

    # --------------------------------------------- data root / settings
    @app.get('/api/settings')
    def get_settings():
        return _root_info(index, settings)

    @app.put('/api/settings/root')
    def set_root(body: RootIn, request: Request):
        """Point the platform at another data folder (persisted).

        Refused while background work still writes into the current root
        (preload encoding / pipeline subprocesses) — those paths were resolved
        against the old root."""
        busy = [f'预加载: {s}' for s in _PRELOAD_REGISTRY.running()]
        busy += [f'{j["label"]} ({j["id"]})' for j in _pipeline.running_jobs()]
        if _acquisition.running():
            st = _acquisition.status()
            busy.append(f'数据采集: {st.get("session")} ({st.get("id")})')
        if busy:
            raise HTTPException(409, {
                'busy': busy,
                'message': '有后台任务正在运行，请等待完成后再切换数据目录：'
                           + '；'.join(busy)})
        previous = str(index.root)
        try:
            new_root = index.set_root(body.path)
        except ValueError as e:
            raise HTTPException(422, {'error': str(e)})
        reset_frame_cache()
        _PRELOAD_REGISTRY.reset()
        settings.remember(new_root)
        settings.save()
        applied = None
        if body.sync_acquisition:
            applied = _write_acquisition_base_dir(str(new_root), sync=True)
        out = _root_info(index, settings)
        out['previous'] = previous
        out['acquisition_applied'] = applied
        return out

    @app.get('/api/fs/drives')
    def fs_drives():
        return {'drives': fs_browse.list_drives()}

    @app.get('/api/fs/list')
    def fs_list(path: str, files: int = 0):
        try:
            return fs_browse.list_dir(path, include_files=bool(files))
        except ValueError as e:
            raise HTTPException(422, {'error': str(e)})

    @app.post('/api/fs/reveal')
    def fs_reveal(body: RootIn, request: Request):
        """Open a folder in the OS file manager (local clients only)."""
        if not _client_is_local(request):
            raise HTTPException(403, 'only local clients may open folders')
        try:
            fs_browse.reveal(body.path)
        except ValueError as e:
            raise HTTPException(422, {'error': str(e)})
        except OSError as e:
            raise HTTPException(500, f'无法打开资源管理器: {e}')
        return {'ok': True}

    @app.put('/api/settings/acquisition')
    def set_acquisition_dir(body: AcqDirIn):
        """Where NEW sessions are recorded (acquisition config ``base_dir``)."""
        value = _write_acquisition_base_dir(body.base_dir,
                                            sync=body.sync_configs)
        if value.get('error'):
            raise HTTPException(422, {'error': value['error']})
        return value

    @app.get('/api/settings/cache')
    def cache_scan():
        """Preview-cache usage per session (stale = session no longer exists)."""
        return scan_cache(index)

    @app.post('/api/settings/cache/clean')
    def cache_clean(body: dict):
        """Delete cached previews of sessions that are gone (confirm=True)."""
        try:
            return clean_cache(index, bool(body.get('confirm')))
        except ValueError as e:
            raise HTTPException(422, {'error': str(e)})

    # --------------------------------------------------- acquisition (T15 ext)
    # Camera binding + recording control. Every endpoint that would grab a
    # camera is refused while a recording runs: the rig has one set of devices
    # and the acquisition holds them exclusively.
    @app.get('/api/acquisition/configs')
    def acq_configs():
        out = []
        for p in ACQ_CONFIGS:
            info = config_cameras(p) if p.is_file() else {'cameras': [],
                                                          'bindings': {}}
            out.append({'path': str(p), 'name': p.name, 'exists': p.is_file(),
                        'base_dir': info.get('base_dir'),
                        'bindings': info.get('bindings', {}),
                        'cameras': info.get('cameras', []),
                        'errors': info.get('errors', [])})
        return {'configs': out, 'launcher': str(ACQ_LAUNCHER)
                if ACQ_LAUNCHER.exists() else None,
                'status': _acquisition.status(),
                'suggested_name': suggest_session_name()}

    @app.get('/api/acquisition/cameras')
    def acq_cameras(config: str):
        return config_cameras(_resolve_config(config))

    @app.post('/api/acquisition/cameras/scan')
    def acq_camera_scan(body: ScanIn):
        if _acquisition.running():
            raise HTTPException(409, {
                'busy_acquisition': True,
                'message': '录制中：相机被采集进程占用，停止后才能扫描'})
        return scan_cameras(body.max_index, body.backend)

    @app.get('/api/acquisition/cameras/preview')
    def acq_camera_preview(index: int, width: int = 640, height: int = 480,
                           backend: str = 'dshow', fourcc: str | None = None):
        if _acquisition.running():
            raise HTTPException(409, {
                'busy_acquisition': True,
                'message': '录制中：相机被采集进程占用，停止后才能预览'})
        width = min(1920, max(32, int(width)))
        height = min(1080, max(32, int(height)))
        try:
            data, meta = preview_jpeg(index, width, height, backend, fourcc)
        except RuntimeError as e:
            raise HTTPException(422, {'error': str(e), 'index': index})
        return Response(content=data, media_type='image/jpeg',
                        headers={'Cache-Control': 'no-store',
                                 'X-Camera-Index': str(index),
                                 'X-Camera-Size': f"{meta['width']}x{meta['height']}"})

    @app.put('/api/acquisition/bindings')
    def acq_set_bindings(body: BindingsIn):
        if _acquisition.running():
            raise HTTPException(409, {
                'busy_acquisition': True,
                'message': '录制中：不能改相机绑定（正在使用这些设备）'})
        p = _resolve_config(body.config)
        try:
            result = set_bindings(p, body.bindings)
        except ValueError as e:
            raise HTTPException(422, {'error': str(e)})
        written = [str(p)]
        if body.sync_configs:
            for other in ACQ_CONFIGS:
                if other == p or not other.is_file():
                    continue
                try:
                    set_bindings(other, body.bindings)
                    written.append(str(other))
                except ValueError as e:
                    # the twin config may not have the same arms/sources — keep
                    # it in sync when possible, never fail the primary write
                    written.append(f'{other} 未同步: {e}')
        return {**result, 'written': written}

    @app.post('/api/acquisition/preflight')
    def acq_preflight(body: PreflightIn):
        if _acquisition.running():
            raise HTTPException(409, {
                'busy_acquisition': True,
                'message': '已有录制在进行中'})
        p = _resolve_config(body.config)
        return preflight(p, body.reserve_minutes, body.session_name,
                         body.check_cameras, body.check_robot)

    @app.post('/api/acquisition/start')
    def acq_start(body: AcqStartIn):
        if _acquisition.running():
            raise HTTPException(409, {
                'busy_acquisition': True,
                'message': '已有录制在进行中，先停止再开始新的录制'})
        p = _resolve_config(body.config)
        try:
            return _acquisition.start(p, body.name, index.log_root, ACQ_STOP_DIR)
        except ValueError as e:
            raise HTTPException(422, {'error': str(e)})
        except OSError as e:
            raise HTTPException(500, {'error': f'启动采集进程失败: {e}'})

    @app.post('/api/acquisition/stop')
    def acq_stop():
        job = _acquisition.current()
        if job is None or job.proc.poll() is not None:
            return {'state': 'idle', 'status': _acquisition.status()}
        result = job.request_stop()
        return {**result, 'status': _acquisition.status()}

    @app.post('/api/acquisition/kill')
    def acq_kill():
        """Emergency: not a graceful stop (data tail may be incomplete)."""
        job = _acquisition.current()
        if job is None or job.proc.poll() is not None:
            return {'state': 'idle', 'status': _acquisition.status()}
        return job.kill()

    @app.get('/api/acquisition/status')
    def acq_status():
        return _acquisition.status()

    @app.get('/api/acquisition/log')
    def acq_log():
        job = _acquisition.current()
        if job is None:
            return Response(content='', media_type='text/plain; charset=utf-8')
        try:
            data = job.log_path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            data = ''
        if len(data) > LOG_TAIL_BYTES:
            data = data[-LOG_TAIL_BYTES:]
        return Response(content='\n'.join(data.splitlines()[-LOG_TAIL_LINES:]),
                        media_type='text/plain; charset=utf-8')

    @app.get('/api/sessions/{name}')
    def session_info(name: str):
        try:
            return index.session_info(name)
        except SessionNotFound as e:
            raise HTTPException(404, str(e))

    # ------------------------------------------------------------ alignment
    @app.get('/api/sessions/{name}/alignment')
    def alignment(name: str, stride: int | None = None, points: int = 2000):
        try:
            return index.motion(name, stride, points)
        except (SessionNotFound, NoAlignment) as e:
            raise HTTPException(404, str(e))

    @app.post('/api/sessions/{name}/align')
    def align_session(name: str):
        """Start on-demand alignment generation (async; idempotent).

        Preconditions (robot data, anchor metadata) are checked synchronously
        so the frontend gets an immediate 422; the heavy computation runs in
        a background thread and progress is polled via
        ``GET /api/sessions/{name}/align/status``.
        """
        from vlgp.web.align_service import start_alignment
        try:
            return start_alignment(index, name)
        except SessionNotFound as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, {'error': str(e)})

    @app.get('/api/sessions/{name}/align/status')
    def align_status(name: str):
        from vlgp.web.align_service import alignment_status
        return alignment_status(name)

    # --------------------------------------------------------- row status
    @app.get('/api/sessions/{name}/row/{row}')
    def row_status(name: str, row: int):
        try:
            return index.row_status(name, row)
        except SessionNotFound as e:
            raise HTTPException(404, str(e))
        except NoAlignment as e:
            raise HTTPException(404, str(e))
        except IndexError as e:
            raise HTTPException(404, str(e))

    # --------------------------------------------------------------- frames
    @app.get('/api/sessions/{name}/frames/{cam}/{row}')
    def frame(name: str, cam: str, row: int, max_width: int = 960):
        if cam not in CAMERA_ORDER:
            raise HTTPException(404, f'unknown camera {cam!r}')
        max_width = min(1280, max(32, int(max_width)))   # clamp (see BatchIn)
        try:
            data = frames.jpeg(name, cam, row, max_width)
        except (SessionNotFound, NoAlignment) as e:
            raise HTTPException(404, str(e))
        except (IndexError, FileNotFoundError, ValueError) as e:
            raise HTTPException(404, str(e))
        return Response(content=data, media_type='image/jpeg',
                        headers={'Cache-Control': 'max-age=3600'})

    @app.post('/api/sessions/{name}/frames/batch')
    def frames_batch(name: str, body: BatchIn):
        """base64 JSON batch — kept for compatibility with older tests only;
        the web player uses /frames/batch-bin (三.7)."""
        if body.cam not in CAMERA_ORDER:
            raise HTTPException(404, f'unknown camera {body.cam!r}')
        try:
            images = frames.batch(name, body.cam, body.rows, body.max_width)
        except (SessionNotFound, NoAlignment) as e:
            raise HTTPException(404, str(e))
        return {'images': images}

    @app.post('/api/sessions/{name}/frames/batch-bin')
    def frames_batch_bin(name: str, body: BatchIn):
        """Binary frame batch (see FrameService.batch_bin) for the web
        player's preload/critical fetches — no base64/JSON on the main
        thread."""
        if body.cam not in CAMERA_ORDER:
            raise HTTPException(404, f'unknown camera {body.cam!r}')
        try:
            data = frames.batch_bin(name, body.cam, body.rows, body.max_width)
        except (SessionNotFound, NoAlignment) as e:
            raise HTTPException(404, str(e))
        return Response(content=data,
                        media_type='application/octet-stream')

    # ------------------------------------------------------- whole-session
    @app.post('/api/sessions/{name}/preload')
    def preload_session(name: str):
        """Start background whole-session preview preload (idempotent);
        progress via GET /api/sessions/{name}/preload/status. Encoded JPEGs
        land in var/web_preview, so the session only needs encoding once."""
        try:
            total = len(frames.index.alignment(name))
        except (SessionNotFound, NoAlignment) as e:
            raise HTTPException(404, str(e))
        return _PRELOAD_REGISTRY.start(frames, name, total)

    @app.get('/api/sessions/{name}/preload/status')
    def preload_status(name: str):
        return _PRELOAD_REGISTRY.status(name)

    # ----------------------------------------------------- pipeline (verify /
    # build / video — run the CLI tools as background subprocess jobs)
    @app.post('/api/sessions/{name}/pipeline/{kind}')
    def pipeline_start(name: str, kind: str, no_copy: bool = False,
                       confirm: bool = False, force: bool = False):
        try:
            session_dir = index.session_dir(name)
        except SessionNotFound as e:
            raise HTTPException(404, str(e))
        if _acquisition.running() and not force:
            # verify/build/video compete for disk and CPU with the recording
            st = _acquisition.status()
            raise HTTPException(409, {
                'warning': True,
                'message': f'数据采集正在进行（{st.get("session")}）：'
                           f'同时跑流水线会抢占磁盘和 CPU，可能影响录制帧率。'
                           f'确认继续？'})
        acq_note = ' [录制中]' if _acquisition.running() else ''
        if kind == 'verify':
            return _pipeline.start('verify', '验证对齐' + acq_note,
                                   ['vlgp.alignment.verify_alignment',
                                    '--session', str(session_dir)],
                                   log_root=index.log_root)
        if kind == 'build':
            argv = ['vlgp.dataset.build_srth_dataset',
                    '--session', str(session_dir)]
            if no_copy:
                argv.append('--no-copy')
            return _pipeline.start('build', '构建数据集' + acq_note, argv,
                                   log_root=index.log_root)
        if kind == 'video':
            # Cameras whose capture directory has no metadata.csv cannot be
            # turned into a video at all (legacy single-camera test sessions
            # carry the endoscope only). Skip them with a log line instead of
            # aborting the whole export halfway through.
            have = [c for c in CAMERA_ORDER
                    if (session_dir / c / 'metadata.csv').exists()]
            missing = [c for c in CAMERA_ORDER if c not in have]
            if not have:
                raise HTTPException(422, {
                    'error': '会话中没有任何相机包含 metadata.csv，无法导出预览视频'})
            header = []
            seq = []
            for cam in have:
                arm = cam.rsplit('_', 1)[-1]
                seq.append(['vlgp.dataset.images_to_video',
                            '--dir', str(session_dir / cam),
                            '--output', str(session_dir / f'arm{arm}_preview.mp4'),
                            '--timestamp-overlay'])
            if missing:
                header.append('跳过无 metadata.csv 的相机: '
                              + ', '.join(missing))
            ann = index.read_annotations(name)
            trim = ann.get('trim') if isinstance(ann, dict) else None
            if (isinstance(trim, dict) and 'start_row' in trim
                    and 'end_row' in trim):
                # export exactly the annotated IN/OUT segment per camera
                args = video_trim_args(index, name, session_dir, trim)
                for cam, cmd in zip(have, seq):
                    a = args.get(cam)
                    if a:
                        cmd += ['--start-sec', f'{a["start_sec"]:.9f}',
                                '--end-sec', f'{a["end_sec"]:.9f}']
                        header.append(
                            f'{cam}: 行 [{trim["start_row"]}, {trim["end_row"]}]'
                            f' -> {a["start_sec"]:.6f}~{a["end_sec"]:.6f} s'
                            f' ({a["source"]})')
                return _pipeline.start_sequence(
                    'video', '导出裁剪区间视频', seq, header_lines=header,
                    log_root=index.log_root)
            if not confirm:
                raise HTTPException(409, {
                    'warning': True,
                    'message': '当前会话未设置 IN/OUT 裁剪点，导出将包含全部预览视频。'
                               '确认继续？'})
            return _pipeline.start_sequence('video', '导出预览视频(全部)', seq,
                                            header_lines=header,
                                            log_root=index.log_root)
        raise HTTPException(404, f'unknown pipeline kind {kind!r}')

    @app.get('/api/pipeline/{job_id}/status')
    def pipeline_status(job_id: str):
        st = _pipeline.status(job_id)
        if st is None:
            raise HTTPException(404, f'unknown job {job_id}')
        return st

    @app.get('/api/pipeline/{job_id}/log')
    def pipeline_log(job_id: str):
        text = _pipeline.log_tail(job_id)
        if text is None:
            raise HTTPException(404, f'unknown job {job_id}')
        return Response(content=text, media_type='text/plain; charset=utf-8')

    # ---------------------------------------------- session metadata (T8 form)
    @app.get('/api/sessions/{name}/meta')
    def session_meta_get(name: str):
        try:
            session_dir = index.session_dir(name)
        except SessionNotFound as e:
            raise HTTPException(404, str(e))
        p = session_dir / 'session_meta.json'
        if p.exists():
            meta = json.loads(p.read_text(encoding='utf-8'))
            return {k: meta.get(k, v) for k, v in DEFAULT_TEMPLATE.items()}
        return dict(DEFAULT_TEMPLATE)

    @app.put('/api/sessions/{name}/meta')
    def session_meta_put(name: str, body: dict):
        try:
            session_dir = index.session_dir(name)
        except SessionNotFound as e:
            raise HTTPException(404, str(e))
        problems = []
        meta = dict(DEFAULT_TEMPLATE)
        for k in DEFAULT_TEMPLATE:
            if k in body:
                meta[k] = body[k]
        if not isinstance(meta['task_type'], str) or meta['task_type'] not in TASK_TYPES:
            problems.append(f"task_type must be one of {TASK_TYPES}")
        for k in ('session_name', 'task_instruction', 'operator', 'organ_model',
                  'camera_calibration', 'notes'):
            if not isinstance(meta[k], str):
                problems.append(f'{k} must be a string')
        if not isinstance(meta['subtask_sequence'], list):
            problems.append('subtask_sequence must be a list')
        if not isinstance(meta['instruments'], dict):
            problems.append('instruments must be a dict')
        if problems:
            raise HTTPException(422, {'errors': problems})
        out = session_dir / 'session_meta.json'
        out.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                       encoding='utf-8')
        return {'ok': True, 'saved': meta}

    # ---------------------------------------------------------- annotations
    @app.get('/api/sessions/{name}/annotations')
    def get_annotations(name: str):
        try:
            return index.read_annotations(name)
        except SessionNotFound as e:
            raise HTTPException(404, str(e))

    @app.put('/api/sessions/{name}/annotations')
    def put_annotations(name: str, body: AnnotationsIn):
        try:
            index.session_dir(name)  # 404 fast if missing
        except SessionNotFound as e:
            raise HTTPException(404, str(e))
        problems = _validate_annotations(body, index, hier, name)
        if problems:
            raise HTTPException(422, {'errors': problems})
        ann = body.model_dump()
        # PyQt5 parity: reviewed_by / reviewed_at generated at save time with
        # identical formatting (os username, local-time ISO seconds).
        if not ann.get('reviewed_by'):
            ann['reviewed_by'] = _os_user()
        if not ann.get('reviewed_at'):
            ann['reviewed_at'] = _local_iso_seconds()
        if ann['status'] == 'rejected' and not ann.get('reject_reason'):
            ann['reject_reason'] = 'rejected in review'
        try:
            index.write_annotations(name, ann)
            index.update_manifest(name, ann['status'], ann['reviewed_at'])
        except OSError as e:
            raise HTTPException(500, f'write failed: {e}')
        return {'ok': True, 'saved': ann}

    @app.get('/api/review/queue')
    def review_queue():
        return index.review_queue()

    @app.get('/api/review/manifest')
    def review_manifest():
        return index.read_manifest()

    # ------------------------------------------------------- task hierarchy
    @app.get('/api/task-hierarchy')
    def task_hierarchy():
        if hier is None:
            raise HTTPException(503, 'task_hierarchy.yaml missing/unreadable')
        return {
            'version': hier.version,
            'status': hier.status,
            'placeholder': hier.is_placeholder(),
            'levels': hier.levels,
            'all_paths': hier.all_paths(),
            'labels': {'/'.join(p): hier.label_for_path(p) for p in hier.all_paths()},
        }

    # ---------------------------------------------------------------- static
    if STATIC_DIR.is_dir():
        app.mount('/', StaticFiles(directory=str(STATIC_DIR), html=True),
                  name='static')

    return app


def _os_user() -> str:
    """Same as vlgp.review.review_session.os_user (USERNAME/USER env)."""
    import os
    return os.environ.get('USERNAME') or os.environ.get('USER') or ''


def _root_info(index: SessionIndex, settings: WebSettings) -> dict:
    """Everything the picker UI needs about the ACTIVE data root."""
    return {
        'root': str(index.root),
        'exists': index.root.is_dir(),
        'writable': fs_browse.is_writable(index.root),
        'cache_dir': str(index.preview_root),
        'log_dir': str(index.log_root),
        'disk': fs_browse.disk_usage(index.root),
        'n_sessions': len(index.list_sessions()),
        'recent_roots': settings.existing_recent(),
        'acquisition': _acquisition_info(),
    }


def _acquisition_info() -> dict:
    """Acquisition output dirs (where NEW sessions get recorded)."""
    configs = []
    for p in ACQ_CONFIGS:
        configs.append({'path': str(p), 'exists': p.exists(),
                        'base_dir': read_base_dir(p) if p.exists() else None})
    primary = next((c['base_dir'] for c in configs if c['base_dir']), None)
    return {'base_dir': primary, 'configs': configs,
            'launcher': str(ACQ_LAUNCHER) if ACQ_LAUNCHER.exists() else None,
            'in_sync': len({c['base_dir'] for c in configs}) == 1}


def _write_acquisition_base_dir(base_dir: str, sync: bool) -> dict:
    """Write ``base_dir`` into the acquisition configs (comment-preserving).

    Relative values are resolved against the repo root, because the
    acquisition process resolves them against its CWD (the launchers cd to the
    repo root) and a stored absolute path keeps working either way.
    """
    raw = (base_dir or '').strip().strip('"')
    if not raw:
        return {'error': '采集输出目录不能为空'}
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        p = p.resolve()
    except OSError as e:
        return {'error': f'无法解析路径 {raw!r}: {e}'}
    created = False
    if not p.is_dir():
        try:
            p.mkdir(parents=True, exist_ok=True)
            created = True
        except OSError as e:
            return {'error': f'目录不存在且无法创建: {p} ({e})'}
    written, failed = [], []
    targets = list(ACQ_CONFIGS) if sync else [ACQ_CONFIGS[0]]
    for cfg in targets:
        if not cfg.exists():
            failed.append({'path': str(cfg), 'error': '配置文件不存在'})
            continue
        try:
            set_base_dir(cfg, str(p))
            written.append(str(cfg))
        except OSError as e:
            failed.append({'path': str(cfg), 'error': str(e)})
    return {'base_dir': str(p), 'created': created, 'written': written,
            'failed': failed, 'acquisition': _acquisition_info()}


def _client_is_local(request: Request) -> bool:
    """Guard for the endpoint that opens OS windows (see /api/fs/reveal).

    The platform is a single-user tool; this only blocks a public address from
    spawning Explorer windows, it is not authentication.
    """
    import ipaddress
    host = request.client.host if request.client else ''
    if host in ('', 'testclient'):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _local_iso_seconds() -> str:
    """PyQt5 parity: local time, UTC offset, seconds precision."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _validate_annotations(body: AnnotationsIn, index: SessionIndex,
                          hier: TaskHierarchy | None, name: str) -> list[str]:
    """Replicate the PyQt5 review tool's invariants + T15 segment checks."""
    problems: list[str] = []
    if body.status not in VALID_STATUS:
        problems.append(f'status must be one of {sorted(VALID_STATUS)}')
    if body.status == 'rejected' and not body.reject_reason:
        problems.append('reject_reason required when status=rejected')
    try:
        n = len(index.alignment(name))
    except NoAlignment:
        return problems + ['no alignment table for this session']
    if body.trim is not None:
        tr = body.trim
        if not isinstance(tr, dict) or not all(k in tr for k in ('start_row', 'end_row')):
            problems.append('trim must have start_row and end_row')
        else:
            s0, s1 = tr.get('start_row'), tr.get('end_row')
            if not (isinstance(s0, int) and isinstance(s1, int)):
                problems.append('trim rows must be ints')
            elif not (0 <= s0 <= s1 < n):
                problems.append(f'trim [{s0}, {s1}] out of range [0, {n})')
    if body.segments:
        if hier is None:
            # vocab missing/corrupt: never silently accept unvalidated segments
            raise HTTPException(
                503, 'task_hierarchy.yaml unavailable — segment validation '
                     'cannot run; fix or restore the vocabulary file')
        problems.extend(f'segments: {p}' for p in hier.validate_segments(body.segments))
        for i, seg in enumerate(body.segments):
            for key in ('start_row', 'end_row'):
                v = seg.get(key)
                if isinstance(v, int) and not (0 <= v < n):
                    problems.append(f'segments[{i}].{key} {v} out of range [0, {n})')
    return problems


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description='VLGP Web platform (T15 v0)')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    app = create_app()
    print(f'VLGP Web platform: http://{args.host}:{args.port}')
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
