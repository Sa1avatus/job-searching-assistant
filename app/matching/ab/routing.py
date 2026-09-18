"""Data-driven routing of ambiguous items to the expensive LLM path.

The cheap ranker (embedding -> cross-encoder -> structured/LTR) orders everything; the LLM is
only worth its cost where the cheap order is unreliable. The unreliable zone is around the
top-k boundary: an item whose score is close to the boundary score can swing in or out of the
shortlist. ``select_for_llm`` routes exactly those items, and ``choose_margin`` picks the margin
from replayed human labels (how many boundary errors a margin captures vs the share of items it
sends to the LLM) instead of a guess.

The router is a pure function; the caller decides what to do with the selected ids and keeps the
baseline path as the fallback if the cheap ranker or the LLM is unavailable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.matching.ltr.baseline import RankingGroup
from app.matching.ltr.metrics import RELEVANT_GAIN

Scorer = Callable[[RankingGroup], Sequence[float]]


def boundary_score(scores: Sequence[float], top_k: int) -> float | None:
    """Midpoint between the last shortlisted score and the first excluded one."""
    if len(scores) <= top_k or top_k < 1:
        return None
    ordered = sorted(scores, reverse=True)
    return (ordered[top_k - 1] + ordered[top_k]) / 2


def select_for_llm(
    ids: Sequence[str], scores: Sequence[float], *, top_k: int, margin: float
) -> list[str]:
    """Ids whose score lies within ``margin`` of the shortlist boundary (deterministic order)."""
    boundary = boundary_score(scores, top_k)
    if boundary is None:
        return []  # everything fits in the shortlist: nothing is ambiguous
    picked = [
        (abs(score - boundary), item_id)
        for item_id, score in zip(ids, scores, strict=True)
        if abs(score - boundary) <= margin
    ]
    return [item_id for _, item_id in sorted(picked)]


@dataclass(slots=True)
class MarginPoint:
    margin: float
    llm_share: float  # fraction of all items sent to the LLM
    error_capture: float | None  # fraction of boundary errors among the routed items


def _boundary_errors(group: RankingGroup, scores: Sequence[float], top_k: int) -> set[str]:
    """Relevant items ranked outside the shortlist and irrelevant ones inside it."""
    ordered = sorted(range(len(scores)), key=lambda i: (-scores[i], group.items[i].vacancy_id))
    inside = set(ordered[:top_k])
    errors = set()
    for index, item in enumerate(group.items):
        relevant = item.gain >= RELEVANT_GAIN
        if (relevant and index not in inside) or (
            not relevant and item.gain == 0 and index in inside
        ):
            errors.add(item.vacancy_id)
    return errors


def margin_curve(
    groups: Sequence[RankingGroup],
    scorer: Scorer,
    margins: Sequence[float],
    *,
    top_k: int = 10,
) -> list[MarginPoint]:
    """For each margin: the LLM share and how many of the cheap ranker's errors it would catch."""
    curve: list[MarginPoint] = []
    usable = [g for g in groups if len(g.items) > 1]
    scored = [(g, list(scorer(g))) for g in usable]
    total_items = sum(len(g.items) for g in usable)
    total_errors = sum(len(_boundary_errors(g, s, top_k)) for g, s in scored)
    for margin in margins:
        routed = 0
        caught = 0
        for group, scores in scored:
            ids = [i.vacancy_id for i in group.items]
            chosen = set(select_for_llm(ids, scores, top_k=top_k, margin=margin))
            routed += len(chosen)
            caught += len(chosen & _boundary_errors(group, scores, top_k))
        curve.append(
            MarginPoint(
                margin=margin,
                llm_share=round(routed / total_items, 4) if total_items else 0.0,
                error_capture=round(caught / total_errors, 4) if total_errors else None,
            )
        )
    return curve


def choose_margin(
    curve: Sequence[MarginPoint], *, min_capture: float = 0.8, max_share: float = 0.3
) -> MarginPoint | None:
    """Smallest margin that captures ``min_capture`` of the errors within the LLM budget.

    None means no margin satisfies both limits: the data does not support a cheap-first design
    at this budget and the LLM path must stay in charge.
    """
    for point in sorted(curve, key=lambda p: p.margin):
        if point.error_capture is not None and point.error_capture >= min_capture:
            return point if point.llm_share <= max_share else None
    return None
