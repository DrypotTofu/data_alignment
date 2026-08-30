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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from vlgp.annotations.task_hierarchy import TaskHierarchy
from vlgp.review.write_session_meta import DEFAULT_TEMPLATE, TASK_TYPES
from vlgp.web.frame_service import FrameService, PreloadRegistry
from vlgp.web.pipeline_service import _pipeline
from vlgp.web.session_index import (
    CAMERA_ORDER,
    NoAlignment,
    SessionIndex,
    SessionNotFound,
)

STATIC_DIR = Path(__file__).parent / 'static'
DEFAULT_PORT = 8765
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


def create_app(index: SessionIndex | None = None) -> FastAPI:
    index = index or SessionIndex()
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
        return {'sessions': index.list_sessions()}

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
    def pipeline_start(name: str, kind: str, no_copy: bool = False):
        try:
            session_dir = index.session_dir(name)
        except SessionNotFound as e:
            raise HTTPException(404, str(e))
        if kind == 'verify':
            return _pipeline.start('verify', '验证对齐',
                                   ['vlgp.alignment.verify_alignment',
                                    '--session', str(session_dir)])
        if kind == 'build':
            argv = ['vlgp.dataset.build_srth_dataset',
                    '--session', str(session_dir)]
            if no_copy:
                argv.append('--no-copy')
            return _pipeline.start('build', '构建数据集', argv)
        if kind == 'video':
            seq = []
            for cam in CAMERA_ORDER:
                arm = cam.rsplit('_', 1)[-1]
                seq.append(['vlgp.dataset.images_to_video',
                            '--dir', str(session_dir / cam),
                            '--output', str(session_dir / f'arm{arm}_preview.mp4'),
                            '--timestamp-overlay'])
            return _pipeline.start_sequence('video', '导出预览视频', seq)
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
