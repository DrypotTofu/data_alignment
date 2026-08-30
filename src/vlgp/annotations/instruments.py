"""Instrument-type code -> Chinese name lookup (T14).

Shared by the dataset builder (``build_srth_dataset``), the review tools and
the web platform, so the code table lives in exactly one place instead of
being imported out of the build tool.
Source: tech_repo/器械编码对照表.md (2026-08-20). Codes outside this table
are kept as raw ids (never abort).
"""
from __future__ import annotations

INSTRUMENT_NAME = {
    0: '无器械/未知器械',
    1: '大号持针钳',
    2: '特大号持针钳',
    3: '大力抓取钳',
    4: '圆头抓取钳',
    5: '直剪刀',
    6: '弯剪刀',
    7: '单极弧剪',
    8: '单极电钩',
    9: '单极电铲',
    10: '双极尖头抓钳',
    11: '双极圆头抓钳',
    12: '超声刀头',
    13: '内窥镜',
}


def instrument_label(code: float) -> str:
    """Return the Chinese name for an instrument_type code, or the raw id."""
    if code == code and int(code) in INSTRUMENT_NAME:  # not NaN
        return INSTRUMENT_NAME[int(code)]
    return f'unknown-{code}'
