"""Deterministic duration evaluator using union intervals.

Calculates actual years of experience from date intervals,
avoiding double-counting of parallel work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class ExperienceInterval:
    """A single experience period."""

    skill: str
    started_at: date
    ended_at: date | None  # None means ongoing
    source_evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class DurationResult:
    """Result of evaluating duration for a claim."""

    claim_id: str
    required_years: float
    actual_years: float
    status: str  # "matched", "partial", "insufficient_evidence", "unknown"
    source_intervals: list[dict[str, object]]
    confidence: float


def _to_day(d: date) -> float:
    """Convert a date to a fractional day count for interval math."""
    return d.toordinal()


def _merge_intervals(
    intervals: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Merge overlapping intervals using union-find approach.

    Input: list of (start_day, end_day) tuples.
    Returns: sorted, non-overlapping merged intervals.
    """
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda pair: pair[0])
    merged: list[tuple[float, float]] = [sorted_intervals[0]]
    for start, end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _total_years(merged: list[tuple[float, float]]) -> float:
    """Sum the lengths of merged intervals in years."""
    total_days = sum(end - start for start, end in merged)
    return round(total_days / 365.25, 2)


def evaluate_duration(
    claim_id: str,
    required_years: float,
    intervals: list[ExperienceInterval],
    *,
    reference_date: date | None = None,
) -> DurationResult:
    """Evaluate whether a set of experience intervals meets a year requirement.

    Parallel intervals are merged (union) to avoid double-counting.
    """
    if required_years <= 0:
        # Unknown or missing required_years — cannot determine satisfaction.
        # Return "unknown" so the pipeline treats this as insufficient evidence
        # rather than falsely confirming a match.
        return DurationResult(
            claim_id=claim_id,
            required_years=required_years,
            actual_years=0.0,
            status="unknown",
            source_intervals=[],
            confidence=0.0,
        )

    if not intervals:
        return DurationResult(
            claim_id=claim_id,
            required_years=required_years,
            actual_years=0.0,
            status="insufficient_evidence",
            source_intervals=[],
            confidence=0.0,
        )

    ref = reference_date or date.today()
    raw_intervals: list[tuple[float, float]] = []
    interval_details: list[dict[str, object]] = []

    for interval in intervals:
        end = interval.ended_at or ref
        if end < interval.started_at:
            continue
        raw_intervals.append((_to_day(interval.started_at), _to_day(end)))
        interval_details.append(
            {
                "skill": interval.skill,
                "started_at": interval.started_at.isoformat(),
                "ended_at": (
                    interval.ended_at.isoformat() if interval.ended_at is not None else None
                ),
                "source_evidence_id": interval.source_evidence_id,
            }
        )

    if not raw_intervals:
        return DurationResult(
            claim_id=claim_id,
            required_years=required_years,
            actual_years=0.0,
            status="insufficient_evidence",
            source_intervals=[],
            confidence=0.0,
        )

    merged = _merge_intervals(raw_intervals)
    actual_years = _total_years(merged)

    if actual_years >= required_years:
        status = "matched"
        confidence = min(1.0, 0.7 + 0.3 * (actual_years / required_years))
    elif actual_years >= required_years * 0.5:
        status = "partial"
        confidence = 0.5 + 0.3 * (actual_years / required_years)
    else:
        status = "partial"
        confidence = 0.3 + 0.2 * (actual_years / required_years)

    return DurationResult(
        claim_id=claim_id,
        required_years=required_years,
        actual_years=actual_years,
        status=status,
        source_intervals=interval_details,
        confidence=round(confidence, 3),
    )
