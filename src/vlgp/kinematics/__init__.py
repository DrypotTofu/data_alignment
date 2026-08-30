"""Modified-DH forward kinematics for the VLGP surgical arms (T16).

The MDH parameter table (`tech_repo/MDH参数表.md`, supplied by the robot
engineer 2026-08-20) defines 13 joints per arm: joints 3-15 of the physical
instrument plus a fixed jaw segment. The acquisition CSV stores ``joint_1..12``
per arm — the mapping between MDH rows and CSV joints, the zero-position
offsets and sign conventions are validated against live PLC output in T16.

The implementation lives in :mod:`vlgp.kinematics.fk`; this package re-exports
the public API so ``vlgp.kinematics.fk`` / ``from vlgp.kinematics import fk``
keep working as before.
"""
from __future__ import annotations

from .fk import (
    ARM_MDH,
    DRIVEN_MDH_ROWS,
    FIXED,
    MDHLink,
    PRISMATIC,
    REVOLUTE,
    fk,
    self_check,
)

__all__ = [
    'ARM_MDH',
    'DRIVEN_MDH_ROWS',
    'FIXED',
    'MDHLink',
    'PRISMATIC',
    'REVOLUTE',
    'fk',
    'self_check',
]


if __name__ == '__main__':
    print('=== MDH FK self-check ===')
    for k, v in self_check().items():
        print(f'  {k}: {v}')
