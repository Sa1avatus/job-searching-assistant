"""Controlled A/B replay of matching variants on identical frozen data.

A *variant* is any callable that ranks one group. Every variant is replayed over the same
evaluation groups, so quality (NDCG@k, Precision@k, Recall@k, MRR), latency (p50/p95 per group),
throughput and memory are directly comparable. Quality is reported next to cost and a variant
that is cheaper but measurably worse is flagged instead of quietly accepted:

* ``accept``               quality within tolerance of the baseline (or better)
* ``trade_off``            cheaper, but quality is lower by more than the tolerance
* ``insufficient_evidence`` too little evaluation data to decide either way

Memory is the peak of Python allocations (tracemalloc) during the replay, CPU time is process
CPU seconds; both are relative measurements for comparing variants on one machine, not absolute
capacity numbers.
"""

from __future__ import annotations

import time
import tracemalloc
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from app.matching.ltr.baseline import RankingGroup
from app.matching.ltr.metrics import (
    mean_defined,
    ndcg_at_k,
    order_by_scores,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

Variant = Callable[[RankingGroup], Sequence[float]]

MIN_GROUPS = 2
MIN_ITEMS = 50
DEFAULT_QUALITY_TOLERANCE = 0.01  # absolute NDCG@10


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


@dataclass(slots=True)
class VariantResult:
    name: str
    ndcg_at_10: float | None
    precision_at_10: float | None
    recall_at_10: float | None
    mrr: float | None
    p50_ms: float
    p95_ms: float
    items_per_second: float
    cpu_seconds: float
    peak_memory_kib: float
    errors: int = 0


@dataclass(slots=True)
class AbReport:
    baseline: str
    n_groups: int
    n_items: int
    results: list[VariantResult]
    verdict: dict[str, str] = field(default_factory=dict)
    ndcg_delta: dict[str, float | None] = field(default_factory=dict)
    speedup: dict[str, float | None] = field(default_factory=dict)


def replay_variant(
    name: str, variant: Variant, groups: Sequence[RankingGroup], *, k: int = 10
) -> VariantResult:
    """Run one variant over all groups; a group that raises counts as an error and is ranked
    by the input order so a crashing challenger cannot look better by being skipped."""
    per_group: list[tuple[float | None, ...]] = []
    latencies: list[float] = []
    errors = 0
    tracemalloc.start()
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    try:
        for group in groups:
            gains = [item.gain for item in group.items]
            ids = [item.vacancy_id for item in group.items]
            started = time.perf_counter()
            try:
                scores = list(variant(group))
                if len(scores) != len(gains):
                    raise ValueError("variant returned a wrong number of scores")
            except Exception:  # noqa: BLE001 - counted, then ranked in input order
                errors += 1
                scores = [-float(i) for i in range(len(gains))]
            latencies.append((time.perf_counter() - started) * 1000)
            ordered = order_by_scores(gains, scores, ids)
            per_group.append(
                (
                    ndcg_at_k(ordered, k),
                    precision_at_k(ordered, k),
                    recall_at_k(ordered, k),
                    reciprocal_rank(ordered),
                )
            )
        wall = time.perf_counter() - wall_start
        cpu = time.process_time() - cpu_start
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    items = sum(len(g.items) for g in groups)
    columns = list(zip(*per_group, strict=True)) if per_group else [(), (), (), ()]
    metrics = [mean_defined(column) for column in columns]
    return VariantResult(
        name=name,
        ndcg_at_10=metrics[0],
        precision_at_10=metrics[1],
        recall_at_10=metrics[2],
        mrr=metrics[3],
        p50_ms=round(percentile(latencies, 0.5), 4),
        p95_ms=round(percentile(latencies, 0.95), 4),
        items_per_second=round(items / wall, 2) if wall > 0 else 0.0,
        cpu_seconds=round(cpu, 4),
        peak_memory_kib=round(peak / 1024, 1),
        errors=errors,
    )


def run_ab(
    variants: Mapping[str, Variant],
    groups: Sequence[RankingGroup],
    *,
    baseline: str,
    k: int = 10,
    tolerance: float = DEFAULT_QUALITY_TOLERANCE,
) -> AbReport:
    """Replay every variant on the same groups and judge each against ``baseline``."""
    if baseline not in variants:
        raise KeyError(f"baseline {baseline!r} is not among the variants")
    usable = [g for g in groups if len(g.items) > 1]
    results = [replay_variant(name, fn, usable, k=k) for name, fn in variants.items()]
    by_name = {r.name: r for r in results}
    base = by_name[baseline]
    n_items = sum(len(g.items) for g in usable)
    enough = len(usable) >= MIN_GROUPS and n_items >= MIN_ITEMS

    report = AbReport(baseline, len(usable), n_items, results)
    for result in results:
        if result.name == baseline:
            continue
        delta = (
            None
            if result.ndcg_at_10 is None or base.ndcg_at_10 is None
            else round(result.ndcg_at_10 - base.ndcg_at_10, 6)
        )
        report.ndcg_delta[result.name] = delta
        report.speedup[result.name] = (
            round(base.p50_ms / result.p50_ms, 3) if result.p50_ms > 0 else None
        )
        if not enough or delta is None:
            report.verdict[result.name] = "insufficient_evidence"
        elif result.errors:
            report.verdict[result.name] = "trade_off"  # crashes are a quality loss
        elif delta >= -tolerance:
            report.verdict[result.name] = "accept"
        else:
            report.verdict[result.name] = "trade_off"
    return report
