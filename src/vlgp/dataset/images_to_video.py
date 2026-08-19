"""Convert a saved image sequence back to a video file.

The script reads `metadata.csv` in the input directory to get the correct frame
order and timestamps. It can either use a user-supplied FPS or compute the
average FPS from the metadata.

Usage:
    python tools/images_to_video.py --dir sessions/<session>/camera_arm_1 \
        --output out.mp4 --fps 30

    # Burn Beijing timestamps into each frame
    python tools/images_to_video.py --dir sessions/<session>/camera_arm_1 \
        --output out.mp4 --timestamp-overlay

Codec notes:
    - mp4v / avc1 : MP4 container (widely compatible)
    - MJPG         : AVI container, lossless-ish per frame, large files
    - XVID         : AVI container
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

import cv2
import numpy as np


from vlgp.acquisition.timestamp import ns_to_beijing_iso8601

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def compute_average_fps(rows: list[dict]) -> float:
    if len(rows) < 2:
        return 30.0
    timestamps = [int(r['timestamp_ns']) for r in rows]
    duration_s = (timestamps[-1] - timestamps[0]) / 1e9
    return (len(timestamps) - 1) / duration_s if duration_s > 0 else 30.0


def draw_timestamp(frame: np.ndarray, ts_ns: int) -> np.ndarray:
    """Overlay Beijing timestamp in the top-left corner."""
    beijing = ns_to_beijing_iso8601(ts_ns) or str(ts_ns)
    cv2.putText(
        frame,
        beijing,
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
        lineType=cv2.LINE_AA,
    )
    return frame


def images_to_video(
    input_dir: Path,
    output_path: Path,
    fps: float | None,
    codec: str,
    timestamp_overlay: bool,
) -> None:
    metadata_path = input_dir / 'metadata.csv'
    if not metadata_path.exists():
        raise FileNotFoundError(f'No metadata.csv found in {input_dir}')

    with open(metadata_path, 'r', encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise ValueError(f'No frames listed in {metadata_path}')

    # Sort by index to ensure correct frame order.
    rows.sort(key=lambda r: int(r['index']))

    if fps is None:
        fps = compute_average_fps(rows)
        logger.info('Using computed average FPS: %.2f', fps)
    else:
        logger.info('Using requested FPS: %.2f', fps)

    # Determine frame size from the first image.
    first_image_path = input_dir / rows[0]['filename']
    first_frame = cv2.imread(str(first_image_path))
    if first_frame is None:
        raise RuntimeError(f'Cannot read first frame: {first_image_path}')
    h, w = first_frame.shape[:2]

    fourcc = cv2.VideoWriter_fourcc(*codec)
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f'Cannot open VideoWriter with codec {codec} for {output_path}')

    written = 0
    for row in rows:
        img_path = input_dir / row['filename']
        frame = cv2.imread(str(img_path))
        if frame is None:
            logger.warning('Skipping unreadable frame: %s', img_path)
            continue

        if timestamp_overlay:
            frame = draw_timestamp(frame, int(row['timestamp_ns']))

        writer.write(frame)
        written += 1

    writer.release()
    logger.info('Wrote %d frames to %s at %.2f fps', written, output_path, fps)


def main() -> int:
    parser = argparse.ArgumentParser(description='Convert saved image sequence to video')
    parser.add_argument('--dir', type=str, required=True,
                        help='Directory containing metadata.csv and images')
    parser.add_argument('--output', type=str, required=True,
                        help='Output video file path')
    parser.add_argument('--fps', type=float, default=None,
                        help='Output FPS (default: compute from metadata)')
    parser.add_argument('--codec', type=str, default='mp4v',
                        help='FourCC codec (default: mp4v; try MJPG for AVI)')
    parser.add_argument('--timestamp-overlay', action='store_true',
                        help='Burn Beijing timestamps into each frame')
    args = parser.parse_args()

    images_to_video(
        input_dir=Path(args.dir),
        output_path=Path(args.output),
        fps=args.fps,
        codec=args.codec,
        timestamp_overlay=args.timestamp_overlay,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
