"""Verify a captured USB camera session.

Reads metadata.csv, overlays timestamps on selected frames, checks frame-rate
consistency, and reports any large gaps.

Usage:
    python verify_capture.py --dir ./capture
    python verify_capture.py --dir ./capture --num-frames 5 --max-gap-ms 100
"""
from __future__ import annotations

import argparse
import csv
import datetime
import logging
from pathlib import Path

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def load_metadata(capture_dir: Path) -> list[dict]:
    meta_path = Path(capture_dir) / 'metadata.csv'
    if not meta_path.exists():
        # Fallback for acquisition framework layout.
        candidates = list(Path(capture_dir).glob('**/metadata.csv'))
        if not candidates:
            raise FileNotFoundError(f'No metadata.csv found under {capture_dir}')
        meta_path = candidates[0]

    rows = []
    with open(meta_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                'index': int(row['index']),
                'timestamp_ns': int(row['timestamp_ns']),
                'timestamp_beijing': row.get('timestamp_beijing'),
                'filename': row['filename'],
                'width': int(row.get('width', 0)) if row.get('width') else None,
                'height': int(row.get('height', 0)) if row.get('height') else None,
            })
    return rows, meta_path.parent


def overlay_info(frame: np.ndarray, ts_ns: int, idx: int, dt_ms: float | None,
                 beijing_str: str | None = None) -> np.ndarray:
    img = frame.copy()
    utc = datetime.datetime.fromtimestamp(ts_ns / 1e9, tz=datetime.timezone.utc)
    lines = [
        f'idx: {idx}',
        f'UTC: {utc.isoformat(timespec="milliseconds")}',
    ]
    if beijing_str:
        lines.append(f'BJ: {beijing_str}')
    lines.append(f'ts_ns: {ts_ns}')
    if dt_ms is not None:
        lines.append(f'dt: {dt_ms:.2f} ms')

    y0 = 30
    for i, line in enumerate(lines):
        y = y0 + i * 30
        cv2.putText(img, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 255, 0), 2)
    return img


def verify(capture_dir: Path, num_frames: int, max_gap_ms: float,
           output_dir: Path | None) -> None:
    rows, image_root = load_metadata(capture_dir)
    if not rows:
        logger.error('No frames in metadata.csv')
        return

    logger.info('Loaded %d frames from %s', len(rows), image_root)
    logger.info('First frame ts: %d', rows[0]['timestamp_ns'])
    logger.info('Last  frame ts: %d', rows[-1]['timestamp_ns'])

    # Compute intervals.
    intervals_ms = []
    large_gaps = []
    for i in range(1, len(rows)):
        dt_ms = (rows[i]['timestamp_ns'] - rows[i - 1]['timestamp_ns']) / 1e6
        intervals_ms.append(dt_ms)
        if dt_ms > max_gap_ms:
            large_gaps.append((i, dt_ms))

    if intervals_ms:
        import statistics
        logger.info('Mean interval: %.2f ms (%.1f fps)', statistics.mean(intervals_ms), 1000 / statistics.mean(intervals_ms))
        logger.info('Std interval : %.2f ms', statistics.stdev(intervals_ms) if len(intervals_ms) > 1 else 0)
        logger.info('Min interval : %.2f ms', min(intervals_ms))
        logger.info('Max interval : %.2f ms', max(intervals_ms))

    if large_gaps:
        logger.warning('Found %d gaps > %.1f ms:', len(large_gaps), max_gap_ms)
        for idx, dt in large_gaps[:10]:
            logger.warning('  frame %d -> %d: %.2f ms', idx - 1, idx, dt)
    else:
        logger.info('No gaps larger than %.1f ms.', max_gap_ms)

    # Overlay timestamps on evenly spaced frames.
    out_dir = Path(output_dir) if output_dir else Path(capture_dir) / 'verified'
    out_dir.mkdir(parents=True, exist_ok=True)

    step = max(1, len(rows) // num_frames)
    selected = [rows[i] for i in range(0, len(rows), step)][:num_frames]

    for row in selected:
        img_path = image_root / row['filename']
        if not img_path.exists():
            logger.warning('Missing image: %s', img_path)
            continue

        frame = cv2.imread(str(img_path))
        if frame is None:
            logger.warning('Failed to read: %s', img_path)
            continue

        idx = row['index']
        dt_ms = intervals_ms[idx] if 0 <= idx < len(intervals_ms) else None
        annotated = overlay_info(frame, row['timestamp_ns'], idx, dt_ms,
                                 beijing_str=row.get('timestamp_beijing'))

        out_name = f'verified_{row["index"]:08d}_{row["timestamp_ns"]}.png'
        out_path = out_dir / out_name
        cv2.imwrite(str(out_path), annotated)
        logger.info('Saved verified frame: %s', out_path)


def main():
    parser = argparse.ArgumentParser(description='Verify captured USB camera frames and timestamps.')
    parser.add_argument('--dir', type=str, default='./capture',
                        help='Directory containing metadata.csv and frames')
    parser.add_argument('--num-frames', type=int, default=5,
                        help='Number of verified frames to generate (default: 5)')
    parser.add_argument('--max-gap-ms', type=float, default=100.0,
                        help='Report intervals larger than this (default: 100 ms)')
    parser.add_argument('--out-dir', type=str, default=None,
                        help='Output directory for verified frames (default: <dir>/verified)')
    args = parser.parse_args()

    verify(Path(args.dir), args.num_frames, args.max_gap_ms,
           Path(args.out_dir) if args.out_dir else None)


if __name__ == '__main__':
    main()
