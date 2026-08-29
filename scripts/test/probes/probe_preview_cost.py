# -*- coding: utf-8 -*-
"""Probe: in-process cost breakdown of the web frame preview path
(frame_service._jpeg_cached cold), and projected throughput with a
ThreadPoolExecutor over per-row encodes."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from vlgp.web.session_index import SessionIndex  # noqa: E402


def _decode_encode(path: Path, max_width: int, reduced: bool) -> bytes:
    flag = cv2.IMREAD_REDUCED_COLOR_2 if reduced else cv2.IMREAD_COLOR
    img = cv2.imread(str(path), flag)
    if img is None:
        raise FileNotFoundError(path)
    h, w = img.shape[:2]
    if w > max_width:
        scale = max_width / w
        img = cv2.resize(img, (max_width, max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise RuntimeError('encode failed')
    return buf.tobytes()


def main() -> int:
    index = SessionIndex()
    for session, rows in (('fps_check', list(range(24))),
                          ('test_20260828_160624', list(range(5000, 5064)))):
        for cam, mw in (('camera_arm_2', 640), ('camera_arm_1', 320)):
            paths = []
            for r in rows:
                fname = index.frame_filename(session, cam, r)
                paths.append(index.session_dir(session) / cam / fname)

            t0 = time.perf_counter()
            for p in paths:
                _decode_encode(p, mw, False)
            serial = (time.perf_counter() - t0) / len(rows)

            for nw in (4,):
                t0 = time.perf_counter()
                with ThreadPoolExecutor(max_workers=nw) as ex:
                    list(ex.map(lambda p: _decode_encode(p, mw, False), paths))
                thr = (time.perf_counter() - t0) / len(rows)
                print(f'{session} {cam} mw={mw}: serial {serial*1e3:5.1f} ms/row | '
                      f'{nw} threads {thr*1e3:5.1f} ms/row ({serial/thr:.1f}x) '
                      f'-> {1/thr:.0f} rows/s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
