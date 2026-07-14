import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def load_robot_data(path):
    """Load robot data from CSV or from the Data_All sheet of an XLSX."""
    path = Path(path)
    if path.suffix.lower() in {'.xlsx', '.xls'}:
        xls = pd.ExcelFile(path)
        if 'Data_All' not in xls.sheet_names:
            raise ValueError(f'"Data_All" sheet not found in {path}')
        df = pd.read_excel(path, sheet_name='Data_All')
    else:
        df = pd.read_csv(path, encoding='utf-8-sig')

    if 'timestamp_utc' not in df.columns:
        raise ValueError('Robot data must contain a "timestamp_utc" column')

    df['timestamp_utc'] = pd.to_datetime(df['timestamp_utc'], utc=True, errors='coerce')
    if df['timestamp_utc'].isna().all():
        raise ValueError('Could not parse robot timestamps')
    return df


def load_video_timestamps(path, time_col='timestamp_utc', frame_col='frame'):
    """Load video frame timestamps from a CSV."""
    df = pd.read_csv(path, encoding='utf-8-sig')

    if frame_col not in df.columns:
        raise ValueError(f'Video timestamp CSV must contain a "{frame_col}" column')
    if time_col not in df.columns:
        raise ValueError(f'Video timestamp CSV must contain a "{time_col}" column')

    df[time_col] = pd.to_datetime(df[time_col], utc=True, errors='coerce')
    if df[time_col].isna().all():
        raise ValueError('Could not parse video timestamps')

    return df[[frame_col, time_col]].rename(columns={time_col: 'video_timestamp_utc'})


def align_nearest(robot_df, video_df, robot_time_col='timestamp_utc'):
    """For each video frame, find the robot row with the nearest timestamp."""
    robot_ts = robot_df[robot_time_col].values.astype('datetime64[ns]')
    video_ts = video_df['video_timestamp_utc'].values.astype('datetime64[ns]')

    # Nearest-neighbor search using numpy broadcasting
    diffs = np.abs(robot_ts[:, None] - video_ts[None, :])
    nearest_idx = np.argmin(diffs, axis=0)

    aligned = robot_df.iloc[nearest_idx].reset_index(drop=True)
    time_diffs = np.min(diffs, axis=0).astype('timedelta64[ns]').astype(np.float64) / 1e9

    video_info = pd.DataFrame({
        'video_frame': video_df['frame'].values,
        'video_timestamp_utc': video_df['video_timestamp_utc'].values,
        'time_diff_s': time_diffs,
    })

    return pd.concat([video_info, aligned], axis=1)


def align_interpolate(robot_df, video_df, robot_time_col='timestamp_utc'):
    """Interpolate robot numeric signals at video frame timestamps.

    This gives smoother results than nearest-neighbor when the robot sampling
    rate is much higher than the video frame rate.
    """
    robot_df = robot_df.sort_values(robot_time_col).reset_index(drop=True)
    numeric_cols = robot_df.select_dtypes(include=[np.number]).columns.tolist()

    # Remove index/timestamp helper columns from interpolation
    skip = {'original_index', 'Name'}
    numeric_cols = [c for c in numeric_cols if c not in skip]

    video_ts_ns = video_df['video_timestamp_utc'].values.astype('int64')
    robot_ts_ns = robot_df[robot_time_col].values.astype('int64')

    result = {
        'video_frame': video_df['frame'].values,
        'video_timestamp_utc': video_df['video_timestamp_utc'].values,
    }

    for col in numeric_cols:
        values = robot_df[col].values
        result[col] = np.interp(video_ts_ns, robot_ts_ns, values)

    return pd.DataFrame(result)


def main():
    parser = argparse.ArgumentParser(
        description='Align sampled robot CSV/XLSX with video frame timestamps.'
    )
    parser.add_argument('robot', help='Robot data CSV or XLSX (Data_All sheet)')
    parser.add_argument('video', help='Video timestamps CSV (columns: frame, timestamp_utc)')
    parser.add_argument('-o', '--output', required=True, help='Output aligned CSV path')
    parser.add_argument('-m', '--method', choices=['nearest', 'interpolate'],
                        default='nearest',
                        help='Alignment method (default: nearest)')
    parser.add_argument('--video-time-col', default='timestamp_utc',
                        help='Column name for video timestamp')
    parser.add_argument('--video-frame-col', default='frame',
                        help='Column name for video frame number')

    args = parser.parse_args()

    print('Loading robot data ...')
    robot_df = load_robot_data(args.robot)
    print(f'  -> {len(robot_df)} robot rows')

    print('Loading video timestamps ...')
    video_df = load_video_timestamps(args.video, args.video_time_col, args.video_frame_col)
    print(f'  -> {len(video_df)} video frames')

    print(f'Aligning using method: {args.method} ...')
    if args.method == 'nearest':
        aligned = align_nearest(robot_df, video_df)
    else:
        aligned = align_interpolate(robot_df, video_df)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    aligned.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f'Wrote {out_path} ({len(aligned)} rows)')


if __name__ == '__main__':
    main()
