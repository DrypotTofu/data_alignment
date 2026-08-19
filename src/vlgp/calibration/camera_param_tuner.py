"""Interactive camera parameter tuner (T11 white-paper calibration).

Opens ONE camera (DSHOW) in a live preview window with OpenCV trackbars so the
operator can find the manual exposure / white-balance operating point where a
white paper is neutral (R/G/B within 10%) and the endoscope-lit red channel
does not clip (< 0.1% pixels >= 250).

Verified DSHOW accepted ranges on the wrist cameras (2026-08-18, index 1/3):
    WB_TEMPERATURE   2800..6500 K   (2100/1600 rejected -> stays 4600)
    EXPOSURE         -12..-1        (positive values rejected -> stays -2)
    BRIGHTNESS       -64..+64       (100/128 rejected -> stays 64)
    CONTRAST / SATURATION accepted as-is.
A "DRIVER REJECTED" flag in the HUD shows any request the driver ignored.

Live HUD on the frame:
    - full-frame R/G/B means and R/B
    - white-paper ROI (largest bright connected component) R/G/B means,
      channel deviation %, R-clip %
    - luminance + all-channel clip % (overexposure warning)
    - current requested -> driver-readback values for every tuned property

Keys:
    s          save current params (+ readbacks) to var/camera_params_<idx>.json
               and print a YAML snippet for default.yaml
    q / ESC    quit

Usage:
    python scripts/tools/camera_param_tuner.py --index 1
    python scripts/tools/camera_param_tuner.py --index 3 --width 1280 --height 960
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]

# (trackbar label, property id, min, max, initial)
# DSHOW readbacks: WB_TEMPERATURE 2800-6500, EXPOSURE -12..-1 on these cameras.
TRACKBARS = [
    ('WB_TEMP', cv2.CAP_PROP_WB_TEMPERATURE, 2800, 6500, 6500),
    ('EXPOSURE', cv2.CAP_PROP_EXPOSURE, -12, -1, -6),
    ('AUTO_EXPOSURE', cv2.CAP_PROP_AUTO_EXPOSURE, 0, 1, 0),
    ('AUTO_WB', cv2.CAP_PROP_AUTO_WB, 0, 1, 0),
    ('BRIGHTNESS', cv2.CAP_PROP_BRIGHTNESS, -64, 64, 0),
    ('CONTRAST', cv2.CAP_PROP_CONTRAST, 0, 128, 32),
    ('SATURATION', cv2.CAP_PROP_SATURATION, 0, 128, 64),
]


class TunerState:
    """Holds requested values; trackbar callbacks mutate this, main loop applies."""

    def __init__(self) -> None:
        self.requested: dict[int, float] = {}
        self.dirty = True

    def set(self, prop: int, value: float) -> None:
        self.requested[prop] = value
        self.dirty = True


state = TunerState()


def _on_trackbar(prop: int, offset: int, value: int) -> None:
    # OpenCV trackbar ranges are 0..(hi-lo); map back to the property's
    # absolute range by adding the lower bound (fixes a bug where absolute
    # values like 6500 were clamped by the trackbar max).
    state.set(prop, float(value + offset))


def paper_roi_stats(frame: np.ndarray) -> dict | None:
    """Stats of the largest bright connected component (the white paper)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mask = (gray > 150).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return None
    areas = [(stats[i, cv2.CC_STAT_AREA], i) for i in range(1, n)]
    areas.sort(reverse=True)
    _, i0 = areas[0]
    x, y, w, h, _ = stats[i0][:5]
    roi = frame[y:y + h, x:x + w]
    b, g, r = roi[:, :, 0].mean(), roi[:, :, 1].mean(), roi[:, :, 2].mean()
    return {
        'x': x, 'y': y, 'w': w, 'h': h, 'area': stats[i0][4],
        'r': r, 'g': g, 'b': b,
        'rb': r / b if b > 0 else 0.0,
    }


def apply_params(cap: cv2.VideoCapture) -> dict:
    """Apply requested values with the correct property order.

    IMPORTANT: AUTO_WB must be set BEFORE WB_TEMPERATURE on these DSHOW
    cameras, otherwise the driver resets the temperature to 4600 (verified
    during T11). Then read back every property for the HUD.
    """
    req = state.requested
    # property order: auto-wb first, then temperature
    if cv2.CAP_PROP_AUTO_WB in req:
        cap.set(cv2.CAP_PROP_AUTO_WB, req[cv2.CAP_PROP_AUTO_WB])
    if cv2.CAP_PROP_WB_TEMPERATURE in req:
        cap.set(cv2.CAP_PROP_WB_TEMPERATURE, req[cv2.CAP_PROP_WB_TEMPERATURE])
    # auto exposure mode flag: 0.25 = manual, 0.75 = auto (OpenCV semantics)
    if cv2.CAP_PROP_AUTO_EXPOSURE in req:
        ae = req[cv2.CAP_PROP_AUTO_EXPOSURE]
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if ae == 0 else 0.75)
    for prop, value in req.items():
        if prop in (cv2.CAP_PROP_AUTO_WB, cv2.CAP_PROP_WB_TEMPERATURE,
                    cv2.CAP_PROP_AUTO_EXPOSURE):
            continue
        cap.set(prop, value)
    # readbacks
    readback = {}
    for label, prop, _, _, _ in TRACKBARS:
        v = cap.get(prop)
        readback[label] = v if v == v else float('nan')
    state.dirty = False
    return readback


def main() -> int:
    parser = argparse.ArgumentParser(description='Interactive camera parameter tuner (T11)')
    parser.add_argument('--index', type=int, required=True, help='Camera index (arm_1=1, arm_3=3)')
    parser.add_argument('--width', type=int, default=1280,
                        help='Requested width (wrist cameras support up to 1280x960)')
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--backend', type=str, default='dshow', choices=['dshow', 'msmf'])
    args = parser.parse_args()

    backend = cv2.CAP_DSHOW if args.backend == 'dshow' else cv2.CAP_MSMF
    cap = cv2.VideoCapture(args.index, backend)
    if not cap.isOpened():
        print(f'ERROR: cannot open camera {args.index} with backend {args.backend}')
        return 1

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)  # manual exposure mode
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    time.sleep(0.3)
    actual = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
              int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    window = f'Camera {args.index} param tuner - s:save q:quit'
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    for label, prop, lo, hi, init in TRACKBARS:
        # OpenCV trackbars are 0..(hi-lo); store init-lo and add lo in the
        # callback so the displayed position matches the absolute property
        # value (previously absolute values were clamped by the trackbar max).
        span = hi - lo
        cv2.createTrackbar(label, window, int(init - lo), span,
                           lambda v, p=prop, off=lo: _on_trackbar(p, off, v))
        state.set(prop, float(init))

    print(f'Camera {args.index} open at {actual[0]}x{actual[1]} ({args.backend})')
    print('Adjust trackbars; HUD shows white-paper neutrality and R-clip. s = save, q/ESC = quit.')

    readback: dict = {}
    last_save = time.time()
    while True:
        ok, frame = cap.read()
        if not ok:
            print('Frame read failed')
            break

        if state.dirty:
            readback = apply_params(cap)

        hud = frame.copy()
        stats = paper_roi_stats(frame)
        lum = float(frame.mean())
        clip_all = np.mean(frame >= 250) * 100
        lum_warn = ' <== OVEREXPOSED (lower EXPOSURE or light)' if lum > 200 else ''
        if stats is not None:
            x, y, w, h = stats['x'], stats['y'], stats['w'], stats['h']
            cv2.rectangle(hud, (x, y), (x + w, y + h), (0, 255, 0), 2)
            mean_c = (stats['r'] + stats['g'] + stats['b']) / 3
            dev = (max(abs(stats['r'] - mean_c), abs(stats['g'] - mean_c),
                       abs(stats['b'] - mean_c)) / mean_c * 100) if mean_c > 0 else 0.0
            clip_r = np.mean(frame[:, :, 2] >= 250) * 100
            lines = [
                f'PAPER ROI: R={stats["r"]:.0f} G={stats["g"]:.0f} B={stats["b"]:.0f}  R/B={stats["rb"]:.3f}',
                f'  channel dev = {dev:.1f}%  (target < 10%)',
                f'  R-clip(>=250) = {clip_r:.3f}%  (target < 0.1%)',
            ]
        else:
            fr = float(frame[:, :, 2].mean())
            fb = float(frame[:, :, 0].mean())
            lines = [f'no bright region found; full-frame R/B = {fr / fb if fb > 0 else 0:.3f}']
        lines.append(f'luminance = {lum:.0f}  clip(all ch) = {clip_all:.2f}%{lum_warn}')
        # property readbacks; flag when the driver rejected the requested value
        for label, prop, _, _, _ in TRACKBARS:
            rb = readback.get(label, float('nan'))
            req = state.requested.get(prop, float('nan'))
            rb_s = f'{rb:.0f}' if rb == rb else 'n/a'
            req_s = f'{req:.0f}' if req == req else 'n/a'
            flag = ''
            if req == req and rb == rb and abs(rb - req) > 1e-6:
                flag = '  <== DRIVER REJECTED (use range below)'
            lines.append(f'{label}: req={req_s}  readback={rb_s}{flag}')

        y0 = 20
        for i, line in enumerate(lines):
            cv2.putText(hud, line, (10, y0 + i * 22), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 255), 2, cv2.LINE_AA)

        cv2.imshow(window, hud)
        key = cv2.waitKey(20) & 0xFF
        if key in (ord('q'), 27):
            break
        if key == ord('s'):
            save_path = REPO_ROOT / 'var' / f'camera_params_{args.index}.json'
            save_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                'camera_index': args.index,
                'backend': args.backend,
                'resolution': list(actual),
                'requested': {label: state.requested[prop] for label, prop, *_ in TRACKBARS
                              if prop in state.requested},
                'readback': {k: (v if v == v else None) for k, v in readback.items()},
            }
            save_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            yaml_snippet = (
                f'camera_index: {args.index}\n'
                f'exposure: {payload["requested"].get("EXPOSURE", -6)}\n'
                f'wb_temperature: {payload["requested"].get("WB_TEMP", 6500)}\n'
                f'auto_wb: {int(payload["requested"].get("AUTO_WB", 0))}\n'
                f'auto_exposure: {int(payload["requested"].get("AUTO_EXPOSURE", 0))}\n'
                f'brightness: {payload["requested"].get("BRIGHTNESS", 0)}\n'
                f'contrast: {payload["requested"].get("CONTRAST", 32)}\n'
                f'saturation: {payload["requested"].get("SATURATION", 64)}\n'
            )
            print(f'\nSaved: {save_path}')
            print('YAML snippet for default.yaml:')
            print(yaml_snippet)
            last_save = time.time()

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
