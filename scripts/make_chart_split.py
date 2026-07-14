import argparse
import datetime
import os
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

import pandas as pd
from xlsxwriter.utility import xl_col_to_name


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------
FILETIME_EPOCH = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)


def filetime_to_utc(filetime):
    """Convert a Windows FILETIME integer to a UTC datetime."""
    try:
        ft = int(float(filetime))
    except (ValueError, TypeError):
        return None
    return FILETIME_EPOCH + datetime.timedelta(seconds=ft / 1e7)


def filetime_to_local(filetime):
    """Convert a Windows FILETIME integer to a local datetime."""
    utc = filetime_to_utc(filetime)
    if utc is None:
        return None
    return utc.astimezone()


# ---------------------------------------------------------------------------
# Column grouping helpers
# ---------------------------------------------------------------------------
GROUP_ORDER = [
    'JA_Arm1', 'JA_Arm2', 'JA_Arm3', 'JA_Arm4',
    'EEF_ArmF', 'EEF_ScopeF', 'EEF_Orient', 'EnergyStat',
    'GripAngle', 'GripSignal', 'InstrType'
]

SHORT_NAMES = {
    'Joint_Angles': 'JA_Arm',
    'EndEffector_Coordinate_ArmFrame': 'EEF_ArmF',
    'EndEffector_Coordinate_ScopeFrame': 'EEF_ScopeF',
    'EndEffector_Orientation': 'EEF_Orient',
    'EnergyInstrument_Status': 'EnergyStat',
    'Gripper_Angle': 'GripAngle',
    'Gripper_Signal': 'GripSignal',
    'Instrument_Type': 'InstrType',
}


def group_key(col_name, name_col='Name'):
    """Map a raw column name to a short group key."""
    if col_name == name_col:
        return None

    # e.g. Joint_Angles[1][1] -> base='Joint_Angles', idx='1'
    m = re.match(r'([A-Za-z_]+)\[(\d+)\]', col_name)
    if not m:
        return col_name

    base, idx = m.group(1), m.group(2)

    # Joint_Angles is split per arm (JA_Arm1, JA_Arm2, ...)
    if base == 'Joint_Angles':
        return f'JA_Arm{idx}'

    return SHORT_NAMES.get(base, base)


def build_groups(columns, name_col='Name'):
    """Group columns according to the predefined order."""
    groups = OrderedDict()
    for c in columns:
        if c == name_col:
            continue
        key = group_key(c, name_col)
        groups.setdefault(key, []).append(c)

    ordered_groups = OrderedDict()
    for k in GROUP_ORDER:
        if k in groups:
            ordered_groups[k] = groups[k]

    # Append any columns that did not match the predefined groups
    for k, v in groups.items():
        if k not in ordered_groups:
            ordered_groups[k] = v

    return ordered_groups


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------
def detect_header_row(src_path, name_col='Name', max_rows=20, min_cols=50):
    """Auto-detect the header row for files with or without metadata prefix.

    We read the file line-by-line manually because the metadata prefix rows
    have inconsistent column counts and can confuse the pandas CSV parser.
    """
    with open(src_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        for idx, line in enumerate(f):
            if idx >= max_rows:
                break
            cells = line.strip().split(',')
            if cells and cells[0].strip() == name_col and len(cells) >= min_cols:
                return idx

    # Fallback: assume the standard raw format with 6 metadata rows.
    return 6


def parse_metadata_rows(src_path, header_row):
    """Read the rows before the data header and return them as a list of lists."""
    rows = []
    with open(src_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        for idx, line in enumerate(f):
            if idx >= header_row:
                break
            rows.append([c.strip() for c in line.rstrip('\r\n').split(',')])
    return rows


def build_metadata_df(metadata_rows):
    """Convert raw metadata rows into a tidy key-value DataFrame.

    Also adds computed UTC/local timestamps when start/end FILETIMEs are found.
    """
    records = []
    start_ft = None
    end_ft = None

    for row in metadata_rows:
        if not row or all(not c for c in row):
            continue
        key = row[0]
        value = ' '.join(row[1:]) if len(row) > 1 else ''
        records.append({'Key': key, 'Value': value})

        if key == 'Starttime of export' and len(row) > 1:
            start_ft = row[1]
        elif key == 'Endtime of export' and len(row) > 1:
            end_ft = row[1]

    # Add computed timestamp fields
    if start_ft:
        t_utc = filetime_to_utc(start_ft)
        t_local = filetime_to_local(start_ft)
        records.append({'Key': 'StartTime_UTC', 'Value': t_utc.isoformat() if t_utc else ''})
        records.append({'Key': 'StartTime_Local', 'Value': t_local.isoformat() if t_local else ''})
    if end_ft:
        t_utc = filetime_to_utc(end_ft)
        t_local = filetime_to_local(end_ft)
        records.append({'Key': 'EndTime_UTC', 'Value': t_utc.isoformat() if t_utc else ''})
        records.append({'Key': 'EndTime_Local', 'Value': t_local.isoformat() if t_local else ''})

    return pd.DataFrame(records)


def add_timestamp_columns(df, start_ft, end_ft, original_index_col='original_index'):
    """Add per-row UTC/local timestamp strings by linear interpolation.

    `df` is assumed to already contain an `original_index` column.
    Timestamps are stored as ISO-8601 strings to avoid Excel timezone issues.
    """
    if start_ft is None or end_ft is None or original_index_col not in df.columns:
        return df

    start_utc = filetime_to_utc(start_ft)
    end_utc = filetime_to_utc(end_ft)
    if start_utc is None or end_utc is None:
        return df

    n_total = df[original_index_col].max() + 1
    if n_total <= 1:
        return df

    duration_s = (end_utc - start_utc).total_seconds()
    dt_per_row = duration_s / (n_total - 1)

    def idx_to_timestamp_strings(idx):
        utc_dt = start_utc + datetime.timedelta(seconds=idx * dt_per_row)
        local_dt = utc_dt.astimezone()
        return utc_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), local_dt.strftime('%Y-%m-%dT%H:%M:%S.%f%z')

    df = df.copy()
    timestamps = df[original_index_col].apply(idx_to_timestamp_strings)
    df['timestamp_utc'] = timestamps.apply(lambda x: x[0])
    df['timestamp_local'] = timestamps.apply(lambda x: x[1])
    return df


def process_file(src_path, out_path, sample_step=10, name_col='Name'):
    """Read a raw CSV and write a sampled Excel workbook with charts."""
    src_path = Path(src_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    print(f'  reading {src_path.name} ...')

    # Different files may have metadata rows or start directly with the header.
    header_row = detect_header_row(src_path, name_col)
    print(f'  -> detected header at row {header_row + 1}')

    # Read metadata rows (everything before the data header).
    metadata_rows = parse_metadata_rows(src_path, header_row)
    metadata_df = build_metadata_df(metadata_rows)

    start_ft = None
    end_ft = None
    for row in metadata_rows:
        if not row:
            continue
        if row[0] == 'Starttime of export' and len(row) > 1:
            start_ft = row[1]
        elif row[0] == 'Endtime of export' and len(row) > 1:
            end_ft = row[1]

    df = pd.read_csv(
        src_path,
        skiprows=header_row,
        encoding='utf-8-sig',
        low_memory=False
    )

    cols = list(df.columns)
    if name_col not in cols:
        raise ValueError(f"expected column '{name_col}' not found in {src_path.name}")

    # Preserve the original row index before downsampling.
    df = df.copy()
    df['original_index'] = df.index

    original_n_rows = len(df)

    # Downsample to reduce workbook size and chart rendering time.
    if sample_step > 1:
        df = df.iloc[::sample_step].reset_index(drop=True)

    # Add per-row timestamps for alignment with video.
    df = add_timestamp_columns(df, start_ft, end_ft)

    # Re-order columns so timestamps come first for convenience.
    front_cols = [name_col, 'original_index', 'timestamp_utc', 'timestamp_local']
    front_cols = [c for c in front_cols if c in df.columns]
    other_cols = [c for c in cols if c != name_col]
    df = df[front_cols + other_cols]

    print(f'  -> {len(df)} rows x {len(cols)} cols after sampling '
          f'({time.time()-t0:.1f}s)')

    # Compute and append approximate sampling rate to metadata.
    if start_ft and end_ft and original_n_rows > 1:
        start_utc = filetime_to_utc(start_ft)
        end_utc = filetime_to_utc(end_ft)
        if start_utc and end_utc:
            duration_s = (end_utc - start_utc).total_seconds()
            sampling_hz = (original_n_rows - 1) / duration_s if duration_s > 0 else None
            if sampling_hz is not None:
                new_rows = [
                    {'Key': 'OriginalRows', 'Value': original_n_rows},
                    {'Key': 'SampledRows', 'Value': len(df)},
                    {'Key': 'SampleStep', 'Value': sample_step},
                    {'Key': 'Duration_s', 'Value': round(duration_s, 3)},
                    {'Key': 'SamplingRate_Hz', 'Value': round(sampling_hz, 3)},
                    {'Key': 'SampleInterval_s', 'Value': round(1.0 / sampling_hz, 6)},
                ]
                metadata_df = pd.concat([metadata_df, pd.DataFrame(new_rows)], ignore_index=True)

    groups = build_groups(cols, name_col)
    for gname, gcols in groups.items():
        print(f'     {gname}: {len(gcols)} cols')

    t1 = time.time()
    print(f'  writing {out_path.name} ...')

    with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
        wb = writer.book
        hdr_fmt = wb.add_format({
            'bold': True,
            'font_name': 'Arial',
            'font_size': 10,
            'bg_color': '#1F4E78',
            'font_color': '#FFFFFF',
            'align': 'center',
            'border': 1
        })

        # 1) Metadata sheet
        if not metadata_df.empty:
            metadata_df.to_excel(writer, sheet_name='Metadata', index=False)
            ws_meta = writer.sheets['Metadata']
            ws_meta.write_row(0, 0, ['Key', 'Value'], hdr_fmt)
            ws_meta.set_column(0, 0, 28)
            ws_meta.set_column(1, 1, 50)

        # 2) All data in one sheet (for alignment / CSV recovery)
        all_cols = [c for c in df.columns]
        df.to_excel(writer, sheet_name='Data_All', index=False)
        ws_all = writer.sheets['Data_All']
        ws_all.write_row(0, 0, all_cols, hdr_fmt)
        ws_all.set_column(0, 3, 22)   # Name, original_index, timestamps
        ws_all.set_column(4, len(all_cols) - 1, 12)
        ws_all.freeze_panes(1, 4)

        for gname, gcols in groups.items():
            sub = df[[name_col] + gcols]
            sub.to_excel(writer, sheet_name=gname, index=False)

            ws = writer.sheets[gname]
            ws.write_row(0, 0, [name_col] + gcols, hdr_fmt)
            ws.set_column(0, 0, 8)
            ws.set_column(1, len(gcols), 12)
            ws.freeze_panes(1, 1)

            nr = len(sub)
            chart = wb.add_chart({'type': 'line'})
            for i, c in enumerate(gcols, start=1):
                chart.add_series({
                    'name':   [gname, 0, i],
                    'values': [gname, 1, i, nr, i],
                    'line':   {'width': 0.75},
                    'marker': {'type': 'none'},
                })

            chart.set_title({'name': f'{gname} ({len(gcols)} series)'})
            chart.set_x_axis({'name': 'Index'})
            chart.set_y_axis({'name': 'Value'})
            chart.set_legend({'position': 'right'})
            anchor = f'{xl_col_to_name(len(gcols) + 2)}2'
            ws.insert_chart(anchor, chart, {'x_scale': 2.5, 'y_scale': 2.0})

    print(f'  -> done in {time.time()-t1:.1f}s | total {time.time()-t0:.1f}s | '
          f'size {out_path.stat().st_size/1024/1024:.1f} MB')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Convert raw surgical-robot CSVs into sampled Excel workbooks with charts.'
    )
    parser.add_argument(
        '-i', '--input-dir',
        type=str,
        default=str(Path(__file__).parent.parent / 'raw_data'),
        help='Directory containing raw CSV files (default: ../raw_data)'
    )
    parser.add_argument(
        '-o', '--output-dir',
        type=str,
        default=str(Path(__file__).parent.parent / 'sample_data'),
        help='Directory for output Excel files (default: ../output)'
    )
    parser.add_argument(
        '-s', '--sample-step',
        type=int,
        default=10,
        help='Keep every N-th row (default: 10)'
    )
    parser.add_argument(
        'files',
        nargs='*',
        help='Optional: specific CSV file(s) to process'
    )

    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if args.files:
        csv_files = [Path(f) for f in args.files]
    else:
        if not input_dir.is_dir():
            print(f'Error: input directory not found: {input_dir}', file=sys.stderr)
            sys.exit(1)
        csv_files = sorted(input_dir.glob('*.csv'))

    if not csv_files:
        print(f'No CSV files found in {input_dir}', file=sys.stderr)
        sys.exit(1)

    print(f'Input dir : {input_dir}')
    print(f'Output dir: {output_dir}')
    print(f'Files to process: {len(csv_files)}')
    print('-' * 60)

    overall_t0 = time.time()
    success = 0
    failed = 0

    for idx, src_path in enumerate(csv_files, start=1):
        # If a relative filename is given, resolve it against input_dir.
        if not src_path.is_absolute() and len(src_path.parts) == 1:
            src_path = input_dir / src_path

        out_name = src_path.stem + '_sampled.xlsx'
        out_path = output_dir / out_name

        print(f'[{idx}/{len(csv_files)}] {src_path.name}')
        try:
            process_file(src_path, out_path, sample_step=args.sample_step)
            success += 1
        except Exception as e:
            print(f'  ERROR processing {src_path.name}: {e}', file=sys.stderr)
            failed += 1

    print('-' * 60)
    print(f'Finished: {success} succeeded, {failed} failed | '
          f'total time {time.time()-overall_t0:.1f}s')

    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
