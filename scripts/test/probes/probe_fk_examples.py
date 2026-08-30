# -*- coding: utf-8 -*-
"""Compute example MDH-FK outputs for the 0830 tech-repo doc."""
import numpy as np

from vlgp.kinematics.fk import ARM_MDH, DRIVEN_MDH_ROWS, fk

print('driven MDH rows (index):', DRIVEN_MDH_ROWS)
print()
for arm in (1, 2, 3, 4):
    p0, _ = fk(arm, [0.0] * 12)
    print(f'arm{arm} zero-pose position (mm): {np.round(p0, 3).tolist()}')
print()
p, r = fk(1, [10, -5, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0])
print('arm1 sample q=[10,-5,20,0,...] position (mm):', np.round(p, 3).tolist())
print('arm1 sample rotation det:', round(float(np.linalg.det(r)), 9))
print()
print('arm1 MDH rows (a, alpha_deg, d, theta_deg, q_type):')
for i, ln in enumerate(ARM_MDH[1]):
    print(f'  [{i}] a={ln.a} alpha={ln.alpha} d={ln.d} theta={ln.theta} {ln.q_type}')
