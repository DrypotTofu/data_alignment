import argparse
import datetime
import re
import sys
from pathlib import Path

import pandas as pd

FILETIME_EPOCH = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)


def filetime_to_utc(filetime):
    try:
        ft = int(float(filetime))
    except (ValueError, TypeError):
        return None
    return FILETIME_EPOCH + datetime.timedelta(seconds=ft / 1e7)


def detect_header_row(src_path, name_col='Name', max_rows=20, min_cols=50):
    with open(src_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        for idx, line in enumerate(f):
            if idx >= max_rows:
                break
            cells = line.strip().split(',')
            if cells and cells[0].strip() == name_col and len(cells) >= min_cols:
                return idx
    return 6


def read_metadata_rows(src_path, header_row):
    rows = []
    with open(src_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        for idx, line in enumerate(f):
            if idx >= header_row:
                break
            rows.append([c.strip() for c in line.rstrip('\r\n').split(',')])
    return rows


def get_start_end_filetime(metadata_rows):
    start_ft = end_ft = None
    for row in metadata_rows:
        if not row:
            continue
        if row[0] == 'Starttime of export' and len(row) > 1:
            start_ft = row[1]
        elif row[0] == 'Endtime of export' and len(row) > 1:
            end_ft = row[1]
    return start_ft, end_ft


def add_timestamps(df, start_ft, end_ft):
    start_utc = filetime_to_utc(start_ft)
    end_utc = filetime_to_utc(end_ft)
    if start_utc is None or end_utc is None or len(df) <= 1:
        return df

    duration_s = (end_utc - start_utc).total_seconds()
    dt_per_row = duration_s / (len(df) - 1)
    df = df.copy()
    df['__timestamp_utc'] = [start_utc + datetime.timedelta(seconds=i * dt_per_row) for i in range(len(df))]
    return df


def parse_time_point(value, start_utc, end_utc, recording_date_local):
    """Parse a user-supplied time point into a UTC datetime.

    Supported formats:
      - #1234              -> row index 1234
      - 10.5  or 10.5s     -> 10.5 seconds after start
      - HH:MM:SS           -> local time on the recording date
      - HH:MM:SS.fff       -> local time with milliseconds
      - YYYY-MM-DDTHH:MM:SS... -> full ISO timestamp (UTC or with tz)
    """
    value = value.strip()

    # Row index: #1234
    if value.startswith('#'):
        try:
            return ('index', int(value[1:]))
        except ValueError:
            raise ValueError(f'Invalid row index: {value}')

    # Relative seconds: 10.5 or 10.5s
    if re.match(r'^\d+(\.\d+)?s?$', value):
        seconds = float(value.rstrip('s'))
        if start_utc is None:
            raise ValueError('Cannot use relative seconds without metadata start time')
        return ('time', start_utc + datetime.timedelta(seconds=seconds))

    # Full ISO timestamp
    dt = None
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z',
                '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S']:
        try:
            dt = datetime.datetime.strptime(value, fmt)
            break
        except ValueError:
            continue

    if dt is not None:
        # If no timezone, assume UTC if full ISO; otherwise local time
        if dt.tzinfo is None:
            if 'T' in value or ' ' in value and len(value.split()[0].split('-')[0]) == 4:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            else:
                dt = dt.astimezone()  # interpret as local time
        return ('time', dt.astimezone(datetime.timezone.utc))

    # Local time of day: HH:MM:SS or HH:MM:SS.fff
    m = re.match(r'^(\d{2}):(\d{2}):(\d{2})(?:\.(\d{3,6}))?$', value)
    if m and recording_date_local is not None:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        frac = m.group(4)
        micro = 0
        if frac:
            frac = (frac + '000000')[:6]
            micro = int(frac)
        dt = recording_date_local.replace(hour=h, minute=mi, second=s, microsecond=micro)
        return ('time', dt.astimezone(datetime.timezone.utc))

    raise ValueError(f'Cannot parse time point: {value}')


def find_row_index(df, time_point, kind):
    if kind == 'index':
        idx = time_point
        if idx < 0:
            idx = 0
        if idx >= len(df):
            idx = len(df) - 1
        return idx

    target = time_point
    ts = df['__timestamp_utc']
    # Find first row with timestamp >= target
    ge = ts[ts >= target]
    if ge.empty:
        return len(df) - 1
    return ge.index[0]


def format_filetime(dt):
    if dt is None:
        return ''
    delta = dt - FILETIME_EPOCH
    return str(int(delta.total_seconds() * 1e7))


def _to_python_dt(dt):
    """Convert pandas Timestamp or any tz-aware datetime to Python datetime."""
    if hasattr(dt, 'to_pydatetime'):
        dt = dt.to_pydatetime()
    return dt


def format_metadata(metadata_rows, segment_name, segment_file, start_dt, end_dt):
    """Return updated metadata rows for a segment."""
    start_dt = _to_python_dt(start_dt) if start_dt else None
    end_dt = _to_python_dt(end_dt) if end_dt else None

    new_rows = []
    for row in metadata_rows:
        if not row:
            new_rows.append(row)
            continue
        key = row[0]
        if key == 'Name':
            new_rows.append([key, segment_name, ''])
        elif key == 'File':
            new_rows.append([key, str(segment_file), ''])
        elif key == 'Starttime of export' and start_dt is not None:
            local = start_dt.astimezone()
            date_str = local.strftime('%Y年%m月%d日')
            time_str = local.strftime('%H:%M:%S.%f')[:-3]
            new_rows.append([key, format_filetime(start_dt), date_str, time_str])
        elif key == 'Endtime of export' and end_dt is not None:
            local = end_dt.astimezone()
            date_str = local.strftime('%Y年%m月%d日')
            time_str = local.strftime('%H:%M:%S.%f')[:-3]
            new_rows.append([key, format_filetime(end_dt), date_str, time_str])
        else:
            new_rows.append(row)
    return new_rows


def split_csv(input_path, segments, out_dir=None):
    input_path = Path(input_path)
    if out_dir is None:
        # Default to a sibling directory named 'coarse_alignment_data'.
        out_dir = input_path.parent.parent / 'coarse_alignment_data'
    else:
        out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    header_row = detect_header_row(input_path)
    metadata_rows = read_metadata_rows(input_path, header_row)
    start_ft, end_ft = get_start_end_filetime(metadata_rows)
    start_utc = filetime_to_utc(start_ft)
    end_utc = filetime_to_utc(end_ft)

    recording_date_local = None
    if start_utc is not None:
        recording_date_local = start_utc.astimezone()

    print(f'Reading {input_path.name} ...')
    df = pd.read_csv(input_path, skiprows=header_row, encoding='utf-8-sig', low_memory=False)
    print(f'  -> {len(df)} data rows')

    df = add_timestamps(df, start_ft, end_ft)
    if '__timestamp_utc' not in df.columns:
        raise ValueError('Cannot compute timestamps; metadata may be missing start/end times.')

    for i, (start_val, end_val, out_name) in enumerate(segments, start=1):
        start_kind, start_tp = parse_time_point(start_val, start_utc, end_utc, recording_date_local)
        end_kind, end_tp = parse_time_point(end_val, start_utc, end_utc, recording_date_local)

        start_idx = find_row_index(df, start_tp, start_kind)
        end_idx = find_row_index(df, end_tp, end_kind)

        if start_idx > end_idx:
            raise ValueError(f'Segment {i}: start ({start_val}) is after end ({end_val})')

        segment = df.iloc[start_idx:end_idx + 1].copy()
        segment_start_dt = segment['__timestamp_utc'].iloc[0]
        segment_end_dt = segment['__timestamp_utc'].iloc[-1]

        # Drop internal timestamp column
        segment = segment.drop(columns=['__timestamp_utc'])

        out_path = out_dir / out_name
        segment_name = out_path.stem

        # Write metadata + blank lines + header + data
        with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
            for row in format_metadata(metadata_rows, segment_name, out_path, segment_start_dt, segment_end_dt):
                f.write(','.join(row) + '\n')
            f.write('\n\n')
            segment.to_csv(f, index=False)

        print(f'  Segment {i}: rows {start_idx} ~ {end_idx} '
              f'({len(segment)} rows) -> {out_path}')


def main():
    parser = argparse.ArgumentParser(
        description='Split a raw robot CSV into multiple segments by time or row index.'
    )
    parser.add_argument('input', help='Input CSV file')
    parser.add_argument(
        'segments',
        nargs='+',
        help='Segment definitions as "start,end,output.csv". '
             'Time formats: #row_index, 10.5s, HH:MM:SS, or ISO timestamp. '
             'Provide multiple segments in order.'
    )
    parser.add_argument('-o', '--out-dir',
                        help='Output directory (default: ../coarse_alignment_data)')

    args = parser.parse_args()

    parsed_segments = []
    for seg in args.segments:
        parts = seg.split(',')
        if len(parts) != 3:
            print(f'Error: each segment must be "start,end,output.csv", got: {seg}', file=sys.stderr)
            sys.exit(1)
        parsed_segments.append(tuple(parts))

    split_csv(args.input, parsed_segments, args.out_dir)


if __name__ == '__main__':
    main()
