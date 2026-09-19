"""Small, honest statistics for the application CRM.

Response/interview/offer rates come from a handful of applications per group, so every rate is
returned with its sample size and a Wilson score interval, and groups too small to say anything
are flagged instead of ranked. Two groups are only called *distinguishable* when their intervals
do not overlap - a descriptive statement about the data, never a causal one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

LOW_SAMPLE = 10  # fewer matured applications than this: report, but do not compare
MATURITY_DAYS = 14  # an application younger than this has not had time to be answered


@dataclass(frozen=True, slots=True)
class RateStat:
    successes: int
    n: int
    value: float | None
    low: float | None
    high: float | None
    low_sample: bool


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    if not 0 <= successes <= n:
        raise ValueError("successes must be between 0 and n")
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def rate(successes: int, n: int) -> RateStat:
    interval = wilson_interval(successes, n)
    return RateStat(
        successes=successes,
        n=n,
        value=round(successes / n, 4) if n else None,
        low=round(interval[0], 4) if interval else None,
        high=round(interval[1], 4) if interval else None,
        low_sample=n < LOW_SAMPLE,
    )


def distinguishable(a: RateStat, b: RateStat) -> bool:
    """True when both rates rest on enough data and their Wilson intervals do not overlap."""
    if a.low_sample or b.low_sample or None in (a.low, a.high, b.low, b.high):
        return False
    assert a.low is not None and a.high is not None and b.low is not None and b.high is not None
    return a.low > b.high or b.low > a.high


def score_band(score: int | float | None) -> str:
    """Production score bucket for grouping only (never a predictor of the outcome here)."""
    if score is None:
        return "unscored"
    lower = min(80, int(score) // 20 * 20)
    return f"{lower}-{lower + 19 if lower < 80 else 100}"
