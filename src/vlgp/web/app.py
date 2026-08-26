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
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vlgp.annotations.task_hierarchy import TaskHierarchy
from vlgp.web.frame_service import FrameService
from vlgp.web.session_index import (
    CAMERA_ORDER,
    NoAlignment,
    SessionIndex,
    SessionNotFound,
)

STATIC_DIR = Path(__file__).parent / 'static'
DEFAULT_PORT = 8765

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


def create_app(index: SessionIndex | None = None) -> FastAPI:
    index = index or SessionIndex()
    frames = FrameService(index)
    hier: TaskHierarchy | None = None
    try:
        hier = TaskHierarchy.load()
    except Exception:
        hier = None

    app = FastAPI(title='VLGP Web Platform', version='0.1.0')
    app.add_middleware(
        CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
        allow_headers=['*'])

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
    def alignment(name: str, stride: int = 1):
        try:
            return index.motion(name, stride)
        except (SessionNotFound, NoAlignment) as e:
            raise HTTPException(404, str(e))

    # --------------------------------------------------------------- frames
    @app.get('/api/sessions/{name}/frames/{cam}/{row}')
    def frame(name: str, cam: str, row: int, max_width: int = 960):
        if cam not in CAMERA_ORDER:
            raise HTTPException(404, f'unknown camera {cam!r}')
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
        if body.cam not in CAMERA_ORDER:
            raise HTTPException(404, f'unknown camera {body.cam!r}')
        try:
            images = frames.batch(name, body.cam, body.rows, body.max_width)
        except (SessionNotFound, NoAlignment) as e:
            raise HTTPException(404, str(e))
        return {'images': images}

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
        try:
            index.write_annotations(name, ann)
        except OSError as e:
            raise HTTPException(500, f'write failed: {e}')
        return {'ok': True, 'saved': ann}

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
    if body.segments and hier is not None:
        problems.extend(f'segments: {p}' for p in hier.validate_segments(body.segments))
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
