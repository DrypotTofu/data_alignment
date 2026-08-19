"""Pack a raw recording session into a SurgSync-style release.

Layout produced under <out>/<session_name>/:

    video/<camera>.mkv            # FFV1 lossless video (bit-exact frames)
    frames/<camera>.parquet       # per-frame timestamps; row i == video frame i
    robot_pose/robot_pose_arm<N>.parquet
    session.json                  # copied verbatim
    episode_meta.json             # typed episode summary (task left for annotation)
    manifest.json                 # SHA-256 of every packed file

Alignment contract: video frame i corresponds to row i of frames/<camera>.parquet;
that row carries the PLC-axis timestamp (timestamp_ns + drift model). Robot joints
at any frame instant are obtained by interpolating the robot_pose parquets (LERP /
SLERP), e.g. via tools/align_camera_anchor.py. The video container is NOT the
sync source — the parquet timestamp table is.

Usage:
    python scripts/tools/pack_session.py --session sessions/test_xxx
    python scripts/tools/pack_session.py --session sessions/test_xxx --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import cv2


from vlgp.acquisition.session_layout import find_per_arm_robot_pose_csvs, legacy_robot_pose_path

FFV1_ARGS = ['-c:v', 'ffv1', '-level', '3', '-coder', '1', '-context', '1',
             '-g', '1', '-slices', '24', '-slicecrc', '1']


def find_ffmpeg() -> str:
    exe = shutil.which('ffmpeg')
    if exe is None:
        raise RuntimeError('ffmpeg not found on PATH (conda install -c conda-forge ffmpeg)')
    return exe


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def _iter_frame_bytes(cam_dir: Path, df: pd.DataFrame):
    """Yield BGR24 bytes for each frame in metadata order."""
    for _, row in df.iterrows():
        p = cam_dir / row['filename']
        if not p.exists():
            raise FileNotFoundError(f'missing frame: {p}')
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f'could not decode frame: {p}')
        yield img


def pack_camera(cam_dir: Path, out_dir: Path, ffmpeg: str, verify: bool) -> dict:
    """Encode one camera directory to FFV1 mkv + frames parquet.

    Frames are decoded with OpenCV and piped to ffmpeg as rawvideo BGR24 —
    this avoids concat/image2 demuxer timestamp quirks and guarantees that the
    encoded frame count and pixel content match the source exactly.
    """
    meta_path = cam_dir / 'metadata.csv'
    df = pd.read_csv(meta_path)
    df = df.sort_values('timestamp_ns').reset_index(drop=True)

    ts = df['timestamp_ns'].values.astype(np.float64)
    avg_fps = float(len(ts) / max((ts[-1] - ts[0]) / 1e9, 1e-9)) if len(ts) > 1 else 30.0

    out_dir.mkdir(parents=True, exist_ok=True)
    mkv_path = out_dir / f'{cam_dir.name}.mkv'
    parquet_path = out_dir.parent / 'frames' / f'{cam_dir.name}.parquet'
    parquet_path.parent.mkdir(parents=True, exist_ok=True)

    width = int(df['width'].iloc[0])
    height = int(df['height'].iloc[0])

    cmd = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{width}x{height}',
        '-r', f'{avg_fps:.6f}', '-i', '-',
        *FFV1_ARGS,
        str(mkv_path),
    ]
    src_hash = hashlib.sha256()
    n_frames = 0
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as proc:
        for img in _iter_frame_bytes(cam_dir, df):
            data = img.tobytes()
            src_hash.update(data)
            proc.stdin.write(data)
            n_frames += 1
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f'ffmpeg encode failed for {cam_dir.name}')

    df_out = df.copy()
    df_out['video_frame_idx'] = range(len(df_out))
    df_out.to_parquet(parquet_path, index=False, compression='zstd')

    result = {
        'camera': cam_dir.name,
        'frames': n_frames,
        'avg_fps': avg_fps,
        'mkv': mkv_path.name,
        'mkv_bytes': mkv_path.stat().st_size,
        'parquet': parquet_path.name,
        'src_sha256': src_hash.hexdigest(),
    }

    if verify:
        dst_hash = hashlib.sha256()
        with subprocess.Popen(
            [ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(mkv_path),
             '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
            stdout=subprocess.PIPE,
        ) as proc:
            nbytes = 0
            while True:
                chunk = proc.stdout.read(1 << 20)
                if not chunk:
                    break
                dst_hash.update(chunk)
                nbytes += len(chunk)
            if proc.wait() != 0:
                raise RuntimeError(f'ffmpeg decode failed for {mkv_path.name}')
        result['decoded_frames'] = nbytes // (width * height * 3)
        result['frame_count_match'] = result['decoded_frames'] == n_frames
        result['bit_exact'] = dst_hash.hexdigest() == result['src_sha256']

    return result


def pack_robot_pose(session_dir: Path, out_dir: Path) -> list[dict]:
    """Convert per-arm TwinCAT CSVs to parquet."""
    pose_root = session_dir / 'robot_pose'
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    per_arm = list(find_per_arm_robot_pose_csvs(session_dir).values())
    if per_arm:
        for csv_path in per_arm:
            arm_name = csv_path.parent.name
            df = pd.read_csv(csv_path)
            out_path = out_dir / f'{arm_name}.parquet'
            df.to_parquet(out_path, index=False, compression='zstd')
            results.append({'arm': arm_name, 'rows': len(df),
                            'parquet': out_path.name, 'bytes': out_path.stat().st_size})
        return results

    legacy = legacy_robot_pose_path(session_dir)
    if legacy is not None:
        df = pd.read_csv(legacy)
        out_path = out_dir / 'robot_pose.parquet'
        df.to_parquet(out_path, index=False, compression='zstd')
        results.append({'arm': 'all', 'rows': len(df),
                        'parquet': out_path.name, 'bytes': out_path.stat().st_size})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description='Pack a raw session into a SurgSync-style release')
    parser.add_argument('--session', type=Path, required=True, help='Session directory')
    parser.add_argument('--out', type=Path, default=None,
                        help='Output root (default: <project>/packed/<session_name>)')
    parser.add_argument('--verify', action='store_true',
                        help='Decode each mkv and verify bit-exact round-trip (slow)')
    args = parser.parse_args()

    session_dir = args.session.resolve()
    if not session_dir.exists():
        print(f'session not found: {session_dir}')
        return 1
    out_dir = (args.out or session_dir.parents[1] / 'packed' / session_dir.name).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()

    print(f'Packing {session_dir.name} -> {out_dir}')

    # Cameras
    cam_results = []
    for cam_dir in sorted(session_dir.glob('camera_*')):
        if not (cam_dir / 'metadata.csv').exists():
            continue
        print(f'  camera {cam_dir.name} ...')
        cam_results.append(pack_camera(cam_dir, out_dir / 'video', ffmpeg, args.verify))
        r = cam_results[-1]
        line = f'    {r["frames"]} frames, {r["mkv_bytes"]/1e6:.1f} MB @ {r["avg_fps"]:.2f} fps'
        if args.verify:
            line += f' | bit_exact={r["bit_exact"]} count_match={r["frame_count_match"]}'
        print(line)

    # Robot poses
    pose_results = pack_robot_pose(session_dir, out_dir / 'robot_pose')
    for r in pose_results:
        print(f'  robot {r["arm"]}: {r["rows"]} rows -> {r["parquet"]} ({r["bytes"]/1e6:.2f} MB)')

    # session.json verbatim
    session_json = session_dir / 'session.json'
    session_info = {}
    if session_json.exists():
        shutil.copy2(session_json, out_dir / 'session.json')
        session_info = json.loads(session_json.read_text(encoding='utf-8'))

    # episode meta
    episode_meta = {
        'episode_id': session_dir.name,
        'task': None,  # filled by the annotation step (workflow_description.json)
        'source_session': str(session_dir),
        'started_at_beijing': session_info.get('started_at_beijing'),
        'stopped_at_beijing': session_info.get('stopped_at_beijing'),
        'duration_s': session_info.get('duration_s'),
        'cameras': [{'name': r['camera'], 'frames': r['frames'],
                     'avg_fps': r['avg_fps'], 'video': f'video/{r["mkv"]}',
                     'timestamps': f'frames/{r["parquet"]}'} for r in cam_results],
        'robot_pose': [{'arm': r['arm'], 'rows': r['rows'],
                        'parquet': f'robot_pose/{r["parquet"]}'} for r in pose_results],
        'alignment_contract': ('video frame i == row i of frames/<camera>.parquet; '
                               'use its PLC timestamp to interpolate robot_pose parquets'),
    }
    (out_dir / 'episode_meta.json').write_text(
        json.dumps(episode_meta, indent=2, ensure_ascii=False), encoding='utf-8')

    # manifest (SHA-256 of every packed file)
    manifest = {}
    for p in sorted(out_dir.rglob('*')):
        if p.is_file() and p.name != 'manifest.json':
            manifest[str(p.relative_to(out_dir))] = sha256_file(p)
    (out_dir / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')

    print(f'\nPacked: {out_dir}')
    print(f'manifest: {len(manifest)} files hashed')

    if args.verify:
        ok = all(r.get('bit_exact') and r.get('frame_count_match') for r in cam_results)
        print(f'VERIFICATION: {"PASS" if ok else "FAIL"}')
        return 0 if ok else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
