"""Replicate preview_single_camera.py's exact property-set order and measure real fps."""
import statistics
import time

import cv2

INDEX = 1


def open_like_preview(backend, w, h, fps, fourcc='MJPG'):
    """Exact same property order as tools/preview_single_camera.py."""
    cap = cv2.VideoCapture(INDEX, backend)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    if fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc[:4]))
    time.sleep(0.5)
    return cap


def measure(cap, duration):
    ts = []
    start = time.time()
    while time.time() - start < duration:
        ret, f = cap.read()
        if ret and f is not None:
            ts.append(time.time())
    return ts


def report(name, cap, ts):
    aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if len(ts) < 10:
        print(f'{name}: actual {aw}x{ah}, NOT ENOUGH FRAMES ({len(ts)})')
        return
    total = ts[-1] - ts[0]
    fps = len(ts) / total
    d = [(ts[i] - ts[i-1]) * 1000.0 for i in range(1, len(ts))]
    s = sorted(d)
    # fps over time (per 5s window) to see ramp-up like the preview shows
    windows = []
    t0 = ts[0]
    for wstart in range(0, int(total), 5):
        n = sum(1 for t in ts if wstart <= (t - t0) < wstart + 5)
        windows.append(n / 5.0)
    print(f'{name}: actual {aw}x{ah}, fps={fps:.2f}, mean={statistics.mean(d):.2f}ms '
          f'std={statistics.stdev(d):.2f}, p95={s[int(len(s)*0.95)]:.2f}, max={max(d):.2f}')
    print(f'    per-5s fps: {["%.1f" % w for w in windows]}')


DUR = 20.0
for backend_name, backend in [('dshow', cv2.CAP_DSHOW), ('msmf', cv2.CAP_MSMF)]:
    for (w, h) in [(1280, 720), (640, 480)]:
        cap = open_like_preview(backend, w, h, 30)
        ts = measure(cap, DUR)
        report(f'{backend_name} {w}x{h} fps_req=30 (preview order)', cap, ts)
        cap.release()
