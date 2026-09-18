"""Hybrid ranking: cheap ranker first, LLM only for the ambiguous zone, baseline as fallback.

Behind ``APP_MATCHING_HYBRID_ROUTING`` (off by default) so rollout is reversible: with the flag
off every group goes through the baseline (today's LLM-heavy pipeline) unchanged.

Failure handling never leaves a group unranked:
* the cheap ranker fails            -> the whole group goes to the baseline;
* the LLM fails for the ambiguous zone -> the cheap order is kept.

The LLM's opinion is merged without mixing score scales: the routed items keep the *set* of cheap
scores they already occupied and only trade places according to the LLM's order, so the rest of
the ranking is untouched.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import structlog

from app.config import Settings
from app.matching.ab.routing import select_for_llm
from app.matching.ltr.baseline import RankingGroup

logger = structlog.get_logger(__name__)

Scorer = Callable[[RankingGroup], Sequence[float]]

PATH_BASELINE = "baseline"
PATH_CHEAP = "cheap"
PATH_HYBRID = "hybrid"
PATH_CHEAP_LLM_FAILED = "cheap_llm_failed"
PATH_BASELINE_FALLBACK = "baseline_fallback"


@dataclass(frozen=True, slots=True)
class HybridResult:
    scores: list[float]
    path: str
    llm_items: int  # how many items the LLM path scored


def _subgroup(group: RankingGroup, indices: Sequence[int]) -> RankingGroup:
    return RankingGroup(group.group_id, tuple(group.items[i] for i in indices))


def hybrid_rank(
    group: RankingGroup,
    *,
    baseline: Scorer,
    cheap: Scorer,
    settings: Settings,
    top_k: int = 10,
) -> HybridResult:
    """Score ``group`` with the configured path; ``baseline`` is the LLM-based pipeline."""
    llm = baseline
    if not settings.matching_hybrid_routing_enabled or settings.matching_llm_margin is None:
        return HybridResult(list(baseline(group)), PATH_BASELINE, len(group.items))

    try:
        cheap_scores = [float(s) for s in cheap(group)]
        if len(cheap_scores) != len(group.items):
            raise ValueError("cheap ranker returned a wrong number of scores")
    except Exception as error:  # noqa: BLE001 - fall back to the proven path
        logger.warning("hybrid_cheap_ranker_failed", error_type=type(error).__name__)
        return HybridResult(list(baseline(group)), PATH_BASELINE_FALLBACK, len(group.items))

    ids = [item.vacancy_id for item in group.items]
    routed_ids = select_for_llm(ids, cheap_scores, top_k=top_k, margin=settings.matching_llm_margin)
    max_items = max(1, int(len(group.items) * settings.matching_llm_max_share))
    routed_ids = routed_ids[:max_items]  # closest to the boundary first; the budget is a hard cap
    if len(routed_ids) < 2:
        return HybridResult(cheap_scores, PATH_CHEAP, 0)

    routed = sorted(ids.index(item_id) for item_id in routed_ids)
    try:
        llm_scores = [float(s) for s in llm(_subgroup(group, routed))]
        if len(llm_scores) != len(routed):
            raise ValueError("LLM path returned a wrong number of scores")
    except Exception as error:  # noqa: BLE001 - keep the cheap order
        logger.warning("hybrid_llm_path_failed", error_type=type(error).__name__)
        return HybridResult(cheap_scores, PATH_CHEAP_LLM_FAILED, 0)

    slots = sorted((cheap_scores[i] for i in routed), reverse=True)
    llm_order = sorted(range(len(routed)), key=lambda n: (-llm_scores[n], ids[routed[n]]))
    merged = list(cheap_scores)
    for slot_score, n in zip(slots, llm_order, strict=True):
        merged[routed[n]] = slot_score
    return HybridResult(merged, PATH_HYBRID, len(routed))
