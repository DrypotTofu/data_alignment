import argparse
import sys
from pathlib import Path

import pandas as pd


def xlsx_to_csv(src_path, out_path=None, sheet_name='Data_All'):
    """Convert a sampled XLSX workbook back into a single CSV table.

    By default the `Data_All` sheet is exported, which contains all signal
    columns plus `original_index`, `timestamp_utc` and `timestamp_local`.
    """
    src_path = Path(src_path)
    if out_path is None:
        out_path = src_path.with_suffix('.csv')
    else:
        out_path = Path(out_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f'Reading {src_path.name} ...')
    xls = pd.ExcelFile(src_path)
    available = xls.sheet_names
    if sheet_name not in available:
        print(f'Error: sheet "{sheet_name}" not found. Available sheets: {available}',
              file=sys.stderr)
        sys.exit(1)

    df = pd.read_excel(src_path, sheet_name=sheet_name)
    print(f'  -> {len(df)} rows x {len(df.columns)} cols')

    df.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f'Wrote {out_path}')


def main():
    parser = argparse.ArgumentParser(
        description='Convert a sampled XLSX back to a single CSV table.'
    )
    parser.add_argument('xlsx', help='Input Excel file')
    parser.add_argument('-o', '--output', help='Output CSV path (default: same name .csv)')
    parser.add_argument('-s', '--sheet', default='Data_All',
                        help='Sheet to export (default: Data_All)')
    args = parser.parse_args()

    xlsx_to_csv(args.xlsx, args.output, args.sheet)


if __name__ == '__main__':
    main()
