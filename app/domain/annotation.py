"""Human annotation rules: labels, pair canonicalisation, review priority, diversity.

Pure domain code. Everything here is deterministic and free of storage concerns so the
rules that protect dataset integrity can be tested exhaustively.

Two facts drive most of the design:

* a pairwise judgement has ONE logical identity regardless of the order the two vacancies
  were shown in, so pairs are stored in canonical order (smaller vacancy id first) and the
  label/reasons are flipped when the submission arrived reversed;
* ``both_equal`` and ``neither`` carry no ordering information, so they must never be
  turned into a (winner, loser) training pair.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

POINTWISE_LABELS = ("relevant", "maybe", "not_relevant")
PAIRWISE_LABELS = ("a_better", "b_better", "both_equal", "neither")
DECISIVE_PAIRWISE_LABELS = frozenset({"a_better", "b_better"})
CONFIDENCE_LEVELS = ("low", "medium", "high")
# Gain per pointwise label for ranking metrics (0 = irrelevant .. 2 = relevant).
POINTWISE_GAIN = {"not_relevant": 0, "maybe": 1, "relevant": 2}

MAX_REASONS = 10
MAX_COMMENT_LENGTH = 2000
_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class InvalidAnnotation(ValueError):
    """A submission breaks a dataset rule; ``code`` is stable for API clients."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_pointwise_label(label: str) -> str:
    if label not in POINTWISE_LABELS:
        raise InvalidAnnotation(
            "invalid_label", f"Pointwise label must be one of {', '.join(POINTWISE_LABELS)}"
        )
    return label


def validate_pairwise_label(label: str) -> str:
    if label not in PAIRWISE_LABELS:
        raise InvalidAnnotation(
            "invalid_label", f"Pairwise preference must be one of {', '.join(PAIRWISE_LABELS)}"
        )
    return label


def validate_confidence(confidence: str | None) -> str | None:
    if confidence is not None and confidence not in CONFIDENCE_LEVELS:
        raise InvalidAnnotation(
            "invalid_confidence", f"Confidence must be one of {', '.join(CONFIDENCE_LEVELS)}"
        )
    return confidence


def normalize_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    text = comment.strip()
    if len(text) > MAX_COMMENT_LENGTH:
        raise InvalidAnnotation(
            "comment_too_long", f"Comment must be at most {MAX_COMMENT_LENGTH} characters"
        )
    return text or None


def normalize_reasons(reasons: Iterable[str]) -> list[str]:
    """Lower-case, validate and de-duplicate reason tags, keeping the first-seen order."""
    seen: dict[str, None] = {}
    for raw in reasons:
        tag = raw.strip().lower()
        if not _REASON_PATTERN.match(tag):
            raise InvalidAnnotation(
                "invalid_reason", f"Reason {raw!r} must be a short snake_case tag"
            )
        seen.setdefault(tag)
    if len(seen) > MAX_REASONS:
        raise InvalidAnnotation("too_many_reasons", f"At most {MAX_REASONS} reasons are allowed")
    return list(seen)


@dataclass(frozen=True, slots=True)
class CanonicalPair:
    first_id: str
    second_id: str
    label: str
    first_reasons: list[str]
    second_reasons: list[str]

    @property
    def pair_key(self) -> str:
        return f"{self.first_id}:{self.second_id}"


def canonicalize_pair(
    vacancy_a_id: str,
    vacancy_b_id: str,
    preference: str,
    a_reasons: list[str],
    b_reasons: list[str],
) -> CanonicalPair:
    """Order a pair canonically, flipping label and reasons so each reason stays attached to
    the vacancy it was written about."""
    validate_pairwise_label(preference)
    if vacancy_a_id == vacancy_b_id:
        raise InvalidAnnotation("same_vacancy", "A pair needs two different vacancies")
    if vacancy_a_id < vacancy_b_id:
        return CanonicalPair(vacancy_a_id, vacancy_b_id, preference, a_reasons, b_reasons)
    flipped = {"a_better": "b_better", "b_better": "a_better"}.get(preference, preference)
    return CanonicalPair(vacancy_b_id, vacancy_a_id, flipped, b_reasons, a_reasons)


def training_pair(
    first_id: str | None, second_id: str | None, label: str
) -> tuple[str, str] | None:
    """(winner, loser) for a stored canonical pair, or None when it states no order."""
    if first_id is None or second_id is None or label not in DECISIVE_PAIRWISE_LABELS:
        return None
    return (first_id, second_id) if label == "a_better" else (second_id, first_id)


# ── review priority ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReviewPriority:
    """Heuristic ordering signal for the annotation queue.

    NOT a probability and NOT calibrated: it only says "look at this one earlier". The
    components and their weights are exposed in ``reasons`` so a reviewer (or a test) can
    see exactly why an item ranks where it does.
    """

    score: float
    reasons: tuple[str, ...]


DISAGREEMENT_WEIGHT = 0.5
UNCERTAINTY_WEIGHT = 0.3
MULTI_STRATUM_WEIGHT = 0.2
KNOWN_STRATA_COUNT = 5


def review_priority(
    *, current_pct: float | None, ltr_pct: float | None, strata: Sequence[str]
) -> ReviewPriority:
    """``current_pct``/``ltr_pct`` are percentile ranks in [0, 1] (1 = best) computed on the
    same candidate pool, so the two rankings are comparable."""
    score = 0.0
    reasons: list[str] = []
    if current_pct is not None and ltr_pct is not None:
        gap = abs(current_pct - ltr_pct)
        score += DISAGREEMENT_WEIGHT * gap
        if gap > 0:
            reasons.append(f"rankings disagree by {gap:.2f} (percentile gap)")
    if current_pct is not None:
        # a vacancy in the middle of the current ranking is the least clear-cut
        uncertainty = 1.0 - abs(2.0 * current_pct - 1.0)
        score += UNCERTAINTY_WEIGHT * uncertainty
        reasons.append(f"mid-ranking uncertainty {uncertainty:.2f}")
    distinct = len(set(strata))
    if distinct:
        score += MULTI_STRATUM_WEIGHT * min(distinct, KNOWN_STRATA_COUNT) / KNOWN_STRATA_COUNT
        reasons.append(f"selected by {distinct} sampling strata: {', '.join(sorted(set(strata)))}")
    return ReviewPriority(round(min(max(score, 0.0), 1.0), 4), tuple(reasons))


def limit_bucket_dominance[T](
    items: Sequence[T], *, bucket: Callable[[T], str], limit: int, max_share: float = 0.3
) -> list[T]:
    """Keep the given order but stop any one bucket (e.g. a company) from filling more than
    ``max_share`` of the queue. Excess items are dropped, not reordered.

    A pool with fewer than ``ceil(1 / max_share)`` distinct buckets cannot be diversified, so
    it is only truncated to ``limit`` (otherwise a one-company pool would be cut to the cap).
    """
    if not 0 < max_share <= 1:
        raise ValueError("max_share must be in (0, 1]")
    if len({bucket(item) for item in items}) < math.ceil(1 / max_share):
        return list(items)[:limit]
    cap = max(1, int(limit * max_share))
    taken: Counter[str] = Counter()
    kept: list[T] = []
    for item in items:
        key = bucket(item)
        if taken[key] >= cap:
            continue
        taken[key] += 1
        kept.append(item)
        if len(kept) >= limit:
            break
    return kept
