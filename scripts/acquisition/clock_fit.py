"""Numerically stable linear clock-drift fitting.

The acquisition timestamps are large int64 nanosecond values. Fitting them
directly with BLAS-backed helpers can lose precision, and some local NumPy
builds fail inside ``polyfit``. This module uses a small pure-Python
least-squares fit around the first timestamp instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class LinearClockFit:
    """Linear mapping ``y = slope * x + intercept`` for timestamp data."""

    slope: float
    intercept: float
    x_origin: float
    y_origin: float
    relative_intercept: float
    residuals: tuple[float, ...]

    @property
    def samples(self) -> int:
        return len(self.residuals)

    @property
    def residual_mean(self) -> float:
        return math.fsum(self.residuals) / self.samples

    @property
    def residual_std(self) -> float:
        mean = self.residual_mean
        return math.sqrt(math.fsum((r - mean) ** 2 for r in self.residuals) / self.samples)

    @property
    def residual_max_abs(self) -> float:
        return max(abs(r) for r in self.residuals)

    def predict(self, x: float) -> float:
        """Predict y with origin-relative arithmetic to reduce cancellation."""
        return self.y_origin + self.relative_intercept + self.slope * (float(x) - self.x_origin)


def fit_linear_timestamps(
    x_values: Iterable[float],
    y_values: Iterable[float],
) -> LinearClockFit:
    """Fit ``y = slope*x + intercept`` without NumPy linear algebra."""
    xs = [float(v) for v in x_values]
    ys = [float(v) for v in y_values]
    if len(xs) != len(ys):
        raise ValueError(f'x/y length mismatch: {len(xs)} != {len(ys)}')
    if len(xs) < 2:
        raise ValueError(f'Need at least 2 samples for linear fit, got {len(xs)}')

    x0 = xs[0]
    y0 = ys[0]
    dxs = [x - x0 for x in xs]
    dys = [y - y0 for y in ys]
    n = len(xs)

    sx = math.fsum(dxs)
    sy = math.fsum(dys)
    sxx = math.fsum(x * x for x in dxs)
    sxy = math.fsum(x * y for x, y in zip(dxs, dys))
    denominator = n * sxx - sx * sx
    if abs(denominator) < 1e-12:
        raise ValueError('Cannot fit clock drift: x values are effectively constant')

    slope = (n * sxy - sx * sy) / denominator
    relative_intercept = (sy - slope * sx) / n
    intercept = y0 + relative_intercept - slope * x0
    residuals = tuple(
        y - (y0 + relative_intercept + slope * dx)
        for y, dx in zip(ys, dxs)
    )

    return LinearClockFit(
        slope=slope,
        intercept=intercept,
        x_origin=x0,
        y_origin=y0,
        relative_intercept=relative_intercept,
        residuals=residuals,
    )
