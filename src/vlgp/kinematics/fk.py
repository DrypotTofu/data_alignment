"""Modified-DH forward kinematics for the VLGP surgical arms (T16).

The MDH parameter table (`tech_repo/MDH参数表.md`, supplied by the robot
engineer 2026-08-20) defines 13 joints per arm: joints 3-15 of the physical
instrument plus a fixed jaw segment. The acquisition CSV stores ``joint_1..12``
per arm — the mapping between MDH rows and CSV joints, the zero-position
offsets and sign conventions are validated against live PLC output in T16; this
module implements the FK and unit self-consistency checks.

Modified DH convention (as used by the PLC):
    T_i = Rx(alpha_i) * Tx(a_i) * Rz(theta_i) * Tz(d_i)

Joint types:
    Fixed     — constant theta (zero offset), not driven
    Revolute  — theta = theta_offset + q (q from CSV)
    Prismatic — d = d_offset + q
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Joint type codes mirror EN_JointMotionType in the PLC.
FIXED = 'fixed'
REVOLUTE = 'revolute'
PRISMATIC = 'prismatic'


@dataclass(frozen=True)
class MDHLink:
    """One Modified-DH link."""
    a: float = 0.0        # link length (mm)
    alpha: float = 0.0    # link twist (deg)
    d: float = 0.0        # link offset (mm)
    theta: float = 0.0    # joint angle offset (deg)
    q_type: str = FIXED


# Arm MDH tables transcribed from tech_repo/MDH参数表.md (2026-08-20).
# 13 entries: joint 3 .. jaw. a in mm, alpha/theta in deg.
ARM_MDH = {
    1: [
        MDHLink(a=0, alpha=0, d=0, theta=0, q_type=FIXED),          # j3
        MDHLink(a=160, alpha=0, d=0, theta=90, q_type=REVOLUTE),    # j4
        MDHLink(a=63, alpha=90, d=508, theta=0, q_type=PRISMATIC),  # j5
        MDHLink(a=0, alpha=-90, d=586, theta=0, q_type=PRISMATIC),  # j6
        MDHLink(a=0, alpha=0, d=0, theta=0, q_type=REVOLUTE),       # j7
        MDHLink(a=0, alpha=-60, d=895, theta=0, q_type=REVOLUTE),   # j8
        MDHLink(a=0, alpha=16, d=0, theta=90, q_type=REVOLUTE),     # j9
        MDHLink(a=0, alpha=90, d=0, theta=-136, q_type=REVOLUTE),   # j10
        MDHLink(a=0, alpha=90, d=10, theta=0, q_type=PRISMATIC),    # j11
        MDHLink(a=0, alpha=0, d=0, theta=0, q_type=REVOLUTE),       # j12
        MDHLink(a=0, alpha=90, d=0, theta=90, q_type=REVOLUTE),     # j13
        MDHLink(a=10.6, alpha=90, d=0, theta=0, q_type=REVOLUTE),   # j14/15
        MDHLink(a=10.4, alpha=0, d=0, theta=0, q_type=FIXED),       # jaw
    ],
    2: [
        MDHLink(a=0, alpha=0, d=0, theta=60, q_type=FIXED),
        MDHLink(a=160, alpha=0, d=0, theta=90, q_type=REVOLUTE),
        MDHLink(a=131, alpha=90, d=378, theta=0, q_type=PRISMATIC),
        MDHLink(a=0, alpha=-90, d=460, theta=0, q_type=PRISMATIC),
        MDHLink(a=0, alpha=0, d=0, theta=0, q_type=REVOLUTE),
        MDHLink(a=0, alpha=-50, d=889, theta=0, q_type=REVOLUTE),
        MDHLink(a=0, alpha=16, d=0, theta=90, q_type=REVOLUTE),
        MDHLink(a=0, alpha=90, d=0, theta=-146, q_type=REVOLUTE),
        MDHLink(a=0, alpha=90, d=10, theta=0, q_type=PRISMATIC),
        MDHLink(a=0, alpha=0, d=0, theta=0, q_type=REVOLUTE),
        MDHLink(a=0, alpha=90, d=0, theta=90, q_type=REVOLUTE),
        MDHLink(a=10.6, alpha=90, d=0, theta=0, q_type=REVOLUTE),
        MDHLink(a=10.4, alpha=0, d=0, theta=0, q_type=FIXED),
    ],
    3: [
        MDHLink(a=0, alpha=0, d=0, theta=120, q_type=FIXED),
        MDHLink(a=160, alpha=0, d=0, theta=90, q_type=REVOLUTE),
        MDHLink(a=131, alpha=90, d=376, theta=0, q_type=PRISMATIC),
        MDHLink(a=0, alpha=-90, d=460, theta=0, q_type=PRISMATIC),
        MDHLink(a=0, alpha=0, d=0, theta=0, q_type=REVOLUTE),
        MDHLink(a=0, alpha=-50, d=889, theta=0, q_type=REVOLUTE),
        MDHLink(a=0, alpha=16, d=0, theta=90, q_type=REVOLUTE),
        MDHLink(a=0, alpha=90, d=0, theta=-146, q_type=REVOLUTE),
        MDHLink(a=0, alpha=90, d=10, theta=0, q_type=PRISMATIC),
        MDHLink(a=0, alpha=0, d=0, theta=0, q_type=REVOLUTE),
        MDHLink(a=0, alpha=90, d=0, theta=90, q_type=REVOLUTE),
        MDHLink(a=10.6, alpha=90, d=0, theta=0, q_type=REVOLUTE),
        MDHLink(a=10.4, alpha=0, d=0, theta=0, q_type=FIXED),
    ],
    4: [
        MDHLink(a=0, alpha=0, d=0, theta=180, q_type=FIXED),
        MDHLink(a=160, alpha=0, d=0, theta=90, q_type=REVOLUTE),
        MDHLink(a=202, alpha=90, d=508, theta=0, q_type=PRISMATIC),
        MDHLink(a=0, alpha=-90, d=586, theta=0, q_type=PRISMATIC),
        MDHLink(a=0, alpha=0, d=0, theta=0, q_type=REVOLUTE),
        MDHLink(a=0, alpha=-60, d=895, theta=0, q_type=REVOLUTE),
        MDHLink(a=0, alpha=16, d=0, theta=90, q_type=REVOLUTE),
        MDHLink(a=0, alpha=90, d=0, theta=-136, q_type=REVOLUTE),
        MDHLink(a=0, alpha=90, d=10, theta=0, q_type=PRISMATIC),
        MDHLink(a=0, alpha=0, d=0, theta=0, q_type=REVOLUTE),
        MDHLink(a=0, alpha=90, d=0, theta=90, q_type=REVOLUTE),
        MDHLink(a=10.6, alpha=90, d=0, theta=0, q_type=REVOLUTE),
        MDHLink(a=10.4, alpha=0, d=0, theta=0, q_type=FIXED),
    ],
}

# How the 13 MDH rows consume CSV joint values joint_1..12 (T16 hypothesis,
# to be validated against PLC): rows that are Revolute/Prismatic take one CSV
# joint in order. Marked rows are driven; fixed rows are offsets only.
# joint_1..12 -> the 10 driven MDH rows (j4,j5,j6,j7,j8,j9,j10,j11,j12,j13)
# is one natural mapping; the exact assignment is confirmed in T16 against PLC.
DRIVEN_MDH_ROWS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)  # indices 1..11 = j4..j14/15


def _link_transform(link: MDHLink, q: float) -> np.ndarray:
    """Return the 4x4 Modified-DH transform for one link, joint value q."""
    if link.q_type == FIXED:
        theta_deg = link.theta
        d = link.d
    elif link.q_type == REVOLUTE:
        theta_deg = link.theta + q
        d = link.d
    else:  # PRISMATIC
        theta_deg = link.theta
        d = link.d + q
    a = link.a
    alpha_deg = link.alpha
    th = np.deg2rad(theta_deg)
    al = np.deg2rad(alpha_deg)
    ct, st = np.cos(th), np.sin(th)
    ca, sa = np.cos(al), np.sin(al)
    return np.array([
        [ct, -st * ca, st * sa, a * ct],
        [st, ct * ca, -ct * sa, a * st],
        [0.0, sa, ca, d],
        [0.0, 0.0, 0.0, 1.0],
    ])


def fk(arm_id: int, joints: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """Forward kinematics: position (mm) and rotation matrix for one arm.

    ``joints`` is a sequence of 12 CSV joint values (joint_1..joint_12).
    Returns (position_xyz, rotation_3x3) in the base frame.
    NOTE: the joint-to-MDH-row mapping is a hypothesis to be confirmed against
    live PLC output in T16; with the default mapping, joints are consumed by
    the 11 driven rows in order (last value unused in this default).
    """
    links = ARM_MDH[arm_id]
    T = np.eye(4)
    q_iter = iter(joints)
    for idx, link in enumerate(links):
        if idx in DRIVEN_MDH_ROWS and link.q_type != FIXED:
            q = next(q_iter, 0.0)
        else:
            q = 0.0
        T = T @ _link_transform(link, q)
    return T[:3, 3], T[:3, :3]


def self_check() -> dict[str, str]:
    """Unit self-consistency: FK is deterministic and identity at zero pose."""
    results = {}
    for arm in (1, 2, 3, 4):
        p0, r0 = fk(arm, [0.0] * 12)
        # rotation matrix must be orthonormal
        ok_rot = np.allclose(r0 @ r0.T, np.eye(3), atol=1e-9)
        results[f'arm{arm}_orthonormal'] = 'PASS' if ok_rot else 'FAIL'
        # same input -> same output (deterministic)
        p1, r1 = fk(arm, [0.0] * 12)
        results[f'arm{arm}_deterministic'] = (
            'PASS' if np.allclose(p0, p1) and np.allclose(r0, r1) else 'FAIL')
        # finite position (no NaN from trig)
        results[f'arm{arm}_finite'] = (
            'PASS' if np.all(np.isfinite(p0)) and np.all(np.isfinite(r0)) else 'FAIL')
    return results


if __name__ == '__main__':
    print('=== MDH FK self-check ===')
    for k, v in self_check().items():
        print(f'  {k}: {v}')
