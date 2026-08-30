"""Session review / trim GUI (T10 v1) — PyQt5 single file.

Review a camera-anchored alignment session and record trim decisions WITHOUT
touching raw data. Decisions go to ``<session>/annotations.json`` (schema in
tech_repo/VLGP_数据清洗与标注工具设计_20260818.md 2.1); build_srth_dataset.py
applies them when building the episode.

Layout:
    endoscope (arm_2) big | wrist_left (arm_1) | wrist_right (arm_3)
    motion curve (|dpos| per arm + gripper_angle), clickable to seek
    [IN] [OUT] status approved/rejected/pending save next

Keys:
    [ / ]     set IN / OUT row
    <- / ->   single-frame seek
    J / K / L shuttle (back / pause / forward, repeat to accelerate)
    S         save annotations
    N         save + next session (batch mode)
    1 / 2 / 3 status: approved / rejected / pending
    Space     play/pause

Usage:
    python -m vlgp.review.review_session --session sessions/20260817_124143
    python -m vlgp.review.review_session --sessions sessions/
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue

import cv2
import numpy as np
import pandas as pd
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

from vlgp.acquisition import session_layout as sl
from vlgp.constants import CAMERA_ORDER, FILENAME_COLS

STATUS_KEYS = {ord('1'): 'approved', ord('2'): 'rejected', ord('3'): 'pending'}

PREFETCH_RADIUS = 90  # frames decoded ahead/behind in the background thread
# Fallback-table camera merge tolerance (ns) for sessions without alignment:
# ~50 ms nearest-frame matching on the PC timestamp axis.
MERGE_TOLERANCE_NS = 50_000_000


class FramePrefetcher(QThread):
    """Background frame decoder with a small ring buffer per camera."""

    ready = pyqtSignal(int)  # grid row now ready in buffers

    def __init__(self, session_dir: Path, align: pd.DataFrame, parent=None):
        super().__init__(parent)
        self.session_dir = session_dir
        self.align = align
        self._wanted: int | None = None
        self._bufs: dict[str, dict[int, np.ndarray]] = {}
        self._cache = {cam: {} for cam in CAMERA_ORDER}
        self._stop = False

    def want(self, row: int) -> None:
        self._wanted = row

    def stop_worker(self) -> None:
        self._stop = True
        self.wait(1000)

    def get(self, cam: str, row: int) -> np.ndarray | None:
        return self._cache.get(cam, {}).get(row)

    def run(self) -> None:
        while not self._stop:
            w = self._wanted
            if w is None:
                self.msleep(10)
                continue
            rows = range(max(0, w - PREFETCH_RADIUS), min(len(self.align), w + PREFETCH_RADIUS + 1))
            # drop rows far from the window to bound memory
            for cam in CAMERA_ORDER:
                keep = set(rows)
                for r in list(self._cache[cam]):
                    if r not in keep:
                        self._cache[cam].pop(r, None)
            for row in rows:
                for cam in CAMERA_ORDER:
                    if row in self._cache[cam]:
                        continue
                    fname = self.align.iloc[row][FILENAME_COLS[cam]]
                    if not isinstance(fname, str):
                        continue
                    img = cv2.imread(str(self.session_dir / cam / fname))
                    if img is not None:
                        self._cache[cam][row] = img
            self.ready.emit(w)
            self.msleep(15)


class ReviewWindow(QMainWindow):
    """Main review window: three sync views + motion curve + controls."""

    def __init__(self, session_dir: Path, batch_mode: bool = False,
                 manifest_path: Path | None = None):
        super().__init__()
        self.session_dir = session_dir
        self.batch_mode = batch_mode
        self.manifest_path = manifest_path

        align_path = sl.alignment_path(session_dir)
        if align_path.exists():
            self.align = pd.read_csv(align_path)
            self.align = self.align.sort_values('anchor_timestamp_ns_plc').reset_index(drop=True)
            self.has_alignment = True
        else:
            # Sessions that failed before alignment (e.g. capture startup
            # issues) still need to be reviewable so they can be rejected.
            # Build a rough table from the cameras' metadata.csv files;
            # timestamps are matched approximately and robot columns are
            # absent (the motion curve simply shows nothing).
            self.align = self._build_fallback_table()
            self.has_alignment = False
        self.n_rows = len(self.align)
        if self.n_rows == 0:
            raise FileNotFoundError(
                f'No alignment CSV and no camera metadata found under {session_dir}.\n'
                f'Nothing to review. Run align_camera_anchor.py first for good sessions.')

        self.annotations = self._load_annotations()
        self.status = self.annotations.get('status', 'pending')
        self.trim = self.annotations.get('trim')
        self.row = 0
        self.playing = False
        self.speed = 1.0
        self._last_frame_t = 0.0

        self._build_ui()
        self._build_motion_curve()
        self._apply_annotations_to_ui()

        self.prefetcher = FramePrefetcher(session_dir, self.align, self)
        self.prefetcher.ready.connect(self._on_prefetch_ready)
        self.prefetcher.start()
        self.prefetcher.want(0)
        self.show_frame(0)

    def _build_fallback_table(self) -> pd.DataFrame:
        """Approximate review table from camera metadata.csv files.

        Used only when the alignment CSV is missing (broken session). The
        camera with the most frames becomes the anchor; the others are
        nearest-timestamp matched within 50 ms. Trim rows recorded in this
        mode do NOT map to build_srth_dataset.py alignment rows — the session
        should normally end up rejected anyway.
        """
        metas = {}
        for cam in CAMERA_ORDER:
            meta_path = self.session_dir / cam / 'metadata.csv'
            if meta_path.exists():
                df = pd.read_csv(meta_path)
                if len(df):
                    metas[cam] = df.sort_values('timestamp_ns').reset_index(drop=True)
        columns = ['anchor_timestamp_ns_plc'] + list(FILENAME_COLS.values())
        if not metas:
            return pd.DataFrame(columns=columns)
        anchor_cam = max(metas, key=lambda c: len(metas[c]))
        table = metas[anchor_cam][['timestamp_ns', 'filename']].rename(columns={
            'timestamp_ns': 'anchor_timestamp_ns_plc',
            'filename': FILENAME_COLS[anchor_cam],
        })
        for cam, df in metas.items():
            if cam == anchor_cam:
                continue
            right = df[['timestamp_ns', 'filename']].rename(
                columns={'filename': FILENAME_COLS[cam]})
            table = pd.merge_asof(
                table, right,
                left_on='anchor_timestamp_ns_plc', right_on='timestamp_ns',
                direction='nearest', tolerance=MERGE_TOLERANCE_NS,
            ).drop(columns=['timestamp_ns'])
        for cam in CAMERA_ORDER:
            if FILENAME_COLS[cam] not in table.columns:
                table[FILENAME_COLS[cam]] = None
        return table

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        title = f'Review — {self.session_dir.name}'
        if not self.has_alignment:
            title += '  [NO ALIGNMENT — fallback view]'
        self.setWindowTitle(title)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # three views
        views = QHBoxLayout()
        self.lbl_endoscope = QLabel('endoscope (arm_2)')
        self.lbl_wrist_left = QLabel('wrist_left (arm_1)')
        self.lbl_wrist_right = QLabel('wrist_right (arm_3)')
        for lbl in (self.lbl_endoscope, self.lbl_wrist_left, self.lbl_wrist_right):
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setMinimumSize(320, 240)
            views.addWidget(lbl)
        root.addLayout(views)

        # motion curve: |dpos| per arm over grid rows
        self.curve = QLabel('motion curve')
        self.curve.setMinimumHeight(140)
        self.curve.setAlignment(Qt.AlignCenter)
        root.addWidget(self.curve)

        # slider
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, self.n_rows - 1)
        self.slider.valueChanged.connect(lambda v: self.show_frame(v))
        root.addWidget(self.slider)

        # controls
        ctrl = QHBoxLayout()
        self.lbl_row = QLabel('row 0 / 0')
        self.btn_in = QPushButton('[IN]')
        self.btn_out = QPushButton('[OUT]')
        self.btn_play = QPushButton('Play')
        self.speed_box = QComboBox()
        self.speed_box.addItems(['0.5x', '1x', '2x', '4x'])
        self.speed_box.setCurrentText('1x')
        self.lbl_status = QLabel('status: pending')
        self.btn_save = QPushButton('Save (S)')
        self.btn_next = QPushButton('Next (N)')
        for w in (self.lbl_row, self.btn_in, self.btn_out, self.btn_play,
                  self.speed_box, self.lbl_status, self.btn_save, self.btn_next):
            ctrl.addWidget(w)
        root.addLayout(ctrl)

        self.btn_in.clicked.connect(lambda: self.set_in_out('in'))
        self.btn_out.clicked.connect(lambda: self.set_in_out('out'))
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_save.clicked.connect(self.save_annotations)
        self.btn_next.clicked.connect(self.next_session)
        self.speed_box.currentTextChanged.connect(self._on_speed)

        self.statusBar().showMessage(str(self.session_dir))

        # Playback timer: time-based advance so wall-clock speed tracks the
        # 30 Hz grid at 1x regardless of UI scheduling jitter.
        self._play_clock = time.perf_counter()
        self._play_frac = 0.0
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(33)
        self.play_timer.timeout.connect(self._tick)
        self.play_timer.start()

    # ---------------------------------------------------------- annotations
    def _load_annotations(self) -> dict:
        p = self.session_dir / 'annotations.json'
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
        return {'version': 1, 'reviewed_by': '', 'reviewed_at': '',
                'status': 'pending', 'reject_reason': '', 'trim': None,
                'segments': [], 'flags': [], 'notes': ''}

    def _apply_annotations_to_ui(self) -> None:
        if self.trim:
            self.row = self.trim.get('start_row', 0)
        self._update_status_label()

    def set_in_out(self, which: str) -> None:
        r = self.row
        if self.trim is None:
            self.trim = {'start_row': r, 'end_row': r}
        if which == 'in':
            self.trim['start_row'] = r
        else:
            self.trim['end_row'] = r
        if self.trim['start_row'] > self.trim['end_row']:
            self.trim['start_row'], self.trim['end_row'] = self.trim['end_row'], self.trim['start_row']
        self.statusBar().showMessage(
            f'IN={self.trim["start_row"]} OUT={self.trim["end_row"]} (press S to save)')

    def _update_status_label(self) -> None:
        self.lbl_status.setText(f'status: {self.status}')
        color = {'approved': '#2e7d32', 'rejected': '#c62828', 'pending': '#555'}[self.status]
        self.lbl_status.setStyleSheet(f'color: {color}; font-weight: bold;')

    def set_status(self, status: str) -> None:
        self.status = status
        self._update_status_label()

    def save_annotations(self) -> None:
        if self.trim is None:
            QMessageBox.warning(self, 'No trim', 'Set [IN] and [OUT] first.')
            return
        ann = {
            'version': 1,
            'reviewed_by': os_user(),
            'reviewed_at': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'),
            'status': self.status,
            'reject_reason': '' if self.status != 'rejected' else 'rejected in review',
            'trim': self.trim,
            'segments': [],
            'flags': [],
            'notes': '',
        }
        out = self.session_dir / 'annotations.json'
        out.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding='utf-8')
        if self.manifest_path is not None:
            self._update_manifest()
        self.statusBar().showMessage(f'Saved {out}')

    def _update_manifest(self) -> None:
        if not self.manifest_path:
            return
        manifest = {}
        if self.manifest_path.exists():
            manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        manifest[self.session_dir.name] = {
            'status': self.status,
            'reviewed_at': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'),
            'has_annotations': True,
        }
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    # -------------------------------------------------------------- frames
    def show_frame(self, row: int) -> None:
        row = int(np.clip(row, 0, self.n_rows - 1))
        self.row = row
        self.slider.blockSignals(True)
        self.slider.setValue(row)
        self.slider.blockSignals(False)
        self.lbl_row.setText(f'row {row} / {self.n_rows - 1}')
        self.prefetcher.want(row)
        self._render(row)
        self._redraw_curve()   # keep the motion-curve marker in sync (single timer)

    def _on_prefetch_ready(self, row: int) -> None:
        if row == self.row:
            self._render(row)

    def _render(self, row: int) -> None:
        imgs = []
        for cam in CAMERA_ORDER:
            img = self.prefetcher.get(cam, row)
            if img is None:
                imgs.append(None)
            else:
                imgs.append(img)
        if imgs[0] is not None:
            self._set_label(self.lbl_endoscope, imgs[0])
        if imgs[1] is not None:
            self._set_label(self.lbl_wrist_left, imgs[1])
        if imgs[2] is not None:
            self._set_label(self.lbl_wrist_right, imgs[2])

    @staticmethod
    def _set_label(lbl: QLabel, bgr: np.ndarray) -> None:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        lbl.setPixmap(QPixmap.fromImage(qimg).scaled(
            lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ------------------------------------------------------------ motion
    def _build_motion_curve(self) -> None:
        """Precompute |dpos| per arm + gripper angle, draw a compact curve."""
        arms = (1, 3)  # wrist arms matter most; anchor is static in demos
        xs = np.arange(self.n_rows)
        lines = []
        for a in arms:
            dx = self.align.get(f'arm{a}_scope_position_x')
            if dx is None:
                continue
            dp = np.sqrt(
                self.align[f'arm{a}_scope_position_x'].diff().fillna(0) ** 2
                + self.align[f'arm{a}_scope_position_y'].diff().fillna(0) ** 2
                + self.align[f'arm{a}_scope_position_z'].diff().fillna(0) ** 2)
            lines.append((f'arm{a} dpos', dp.values))
        ga = self.align.get('arm1_gripper_angle')
        if ga is not None:
            lines.append(('gripper arm1', ga.values))
        self._motion_lines = lines
        self._curve_row = 0
        self._redraw_curve()

    def _redraw_curve(self) -> None:
        W, H = 1280, 140
        img = np.full((H, W, 3), 20, np.uint8)
        n = self.n_rows
        if n > 1 and self._motion_lines:
            xs = np.linspace(0, W - 1, n)
            for (name, vals) in self._motion_lines:
                v = np.asarray(vals, dtype=np.float64)
                v = np.nan_to_num(v, nan=0.0)
                mx = np.nanmax(v) if np.nanmax(v) > 0 else 1.0
                ys = H - 10 - (v / mx) * (H - 30)
                ys = np.clip(ys, 0, H - 1)
                pts = np.stack([xs, ys], axis=1).astype(np.int32)
                color = {'arm1 dpos': (255, 180, 60), 'arm3 dpos': (120, 220, 120),
                         'gripper arm1': (120, 160, 255)}.get(name, (200, 200, 200))
                cv2.polylines(img, [pts], False, color, 1, cv2.LINE_AA)
        # current position marker
        if n > 1:
            x = int(self.row / (n - 1) * (W - 1))
            cv2.line(img, (x, 0), (x, H), (255, 255, 255), 1)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, W, H, 3 * W, QImage.Format_RGB888).copy()
        self.curve.setPixmap(QPixmap.fromImage(qimg))

    # ------------------------------------------------------------ playback
    def toggle_play(self) -> None:
        self.playing = not self.playing
        self.btn_play.setText('Pause' if self.playing else 'Play')
        if self.playing:
            self._play_clock = time.perf_counter()
            self._play_frac = 0.0

    def _on_speed(self, text: str) -> None:
        self.speed = float(text.rstrip('x'))

    def _tick(self) -> None:
        if not self.playing:
            return
        now = time.perf_counter()
        dt = now - self._play_clock
        self._play_clock = now
        self._play_frac += dt * self.speed * 30.0  # 30 grid rows/s at 1x
        step = int(self._play_frac)
        if step < 1:
            return
        self._play_frac -= step
        nxt = self.row + step
        if nxt >= self.n_rows:
            nxt = self.n_rows - 1
            self.playing = False
            self.btn_play.setText('Play')
        self.show_frame(nxt)

    def next_session(self) -> None:
        if self.batch_mode:
            self.save_annotations()
            self.close()

    # -------------------------------------------------------------- events
    def keyPressEvent(self, event) -> None:
        k = event.key()
        if k == Qt.Key_BracketLeft:
            self.set_in_out('in')
        elif k == Qt.Key_BracketRight:
            self.set_in_out('out')
        elif k == Qt.Key_Left:
            self.show_frame(self.row - 1)
        elif k == Qt.Key_Right:
            self.show_frame(self.row + 1)
        elif k == Qt.Key_J:
            self.playing = False
            self.btn_play.setText('Play')
            self.show_frame(self.row - max(1, int(self.speed * 5)))
        elif k == Qt.Key_K:
            self.toggle_play()
        elif k == Qt.Key_L:
            self.playing = False
            self.btn_play.setText('Play')
            self.show_frame(self.row + max(1, int(self.speed * 5)))
        elif k == Qt.Key_S:
            self.save_annotations()
        elif k == Qt.Key_N:
            self.next_session()
        elif k == Qt.Key_Space:
            self.toggle_play()
        elif k in STATUS_KEYS:
            self.set_status(STATUS_KEYS[k])
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.playing = False
        self.prefetcher.stop_worker()
        super().closeEvent(event)


def os_user() -> str:
    import os
    return os.environ.get('USERNAME') or os.environ.get('USER') or ''


def collect_sessions(root: Path) -> list[Path]:
    """Ordered list of session dirs with an alignment CSV (batch queue)."""
    out = []
    if root.exists():
        for d in sorted(root.iterdir()):
            if d.is_dir() and sl.alignment_path(d).exists():
                out.append(d)
    return out


def _resolve_session(path: Path) -> Path:
    """Resolve a session dir from any working directory.

    Tries (in order): as-given, <repo>/<path>, <cwd>/<path>. Returns the first
    existing directory; falls back to the absolute as-given path.
    """
    candidates = [Path(path)]
    if not path.is_absolute():
        candidates.append(REPO_ROOT / path)
        candidates.append(Path.cwd() / path)
    for cand in candidates:
        if cand.is_dir():
            return cand.resolve()
    return Path(path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description='Session review / trim GUI (T10 v1)')
    parser.add_argument('--session', type=Path, default=None, help='Single session dir')
    parser.add_argument('--sessions', type=Path, default=None,
                        help='Sessions root for batch queue mode')
    args = parser.parse_args()

    app = QApplication(sys.argv)
    if args.session:
        w = ReviewWindow(_resolve_session(args.session))
        w.show()
    elif args.sessions:
        root = args.sessions
        if not root.is_absolute():
            root = REPO_ROOT / root
        root = root.resolve()
        sessions = collect_sessions(root)
        if not sessions:
            print(f'No sessions with alignment found under {root}')
            return 1
        manifest = root / 'review_manifest.json'
        pending = []
        if manifest.exists():
            m = json.loads(manifest.read_text(encoding='utf-8'))
        else:
            m = {}
        for s in sessions:
            if m.get(s.name, {}).get('status') not in ('approved', 'rejected'):
                pending.append(s)
        if not pending:
            print('All sessions reviewed. Re-run to review a specific one.')
            return 0
        w = ReviewWindow(pending[0], batch_mode=True, manifest_path=manifest)
        w.show()
    else:
        parser.error('Provide --session or --sessions')

    # NOTE: no second playback timer here — ReviewWindow owns the single
    # play_timer (33 ms -> _tick); the curve marker is redrawn inside
    # show_frame. A second timer (as once existed) drove _tick twice and
    # played back at ~2x speed.
    return app.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
