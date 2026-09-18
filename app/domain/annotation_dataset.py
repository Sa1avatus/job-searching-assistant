"""Leakage-safe train/validation/test folds and coverage rules for the human label dataset.

Pure and deterministic. A random per-row split would leak: the same vacancy (or two
postings of one company, or two vacancies a human compared head to head) would sit in train
and test and inflate every metric. Folds are therefore assigned to *groups*:

* every vacancy of one company is one group (reposts and near-duplicates travel together);
* vacancies that appear in a pairwise judgement are merged into one group;
* a group's fold is a hash of its id, so it never depends on row order or insertion time.

An evaluation split can be **frozen**: its validation/test vacancies are recorded once, and
from then on any group touching them is pinned to that evaluation fold (test wins over
validation) and can never enter train. Retraining can therefore never see the frozen
evaluation data, however many labels are added later.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

TRAIN, VALIDATION, TEST = "train", "validation", "test"
FOLDS = (TRAIN, VALIDATION, TEST)
EVAL_FOLDS = (VALIDATION, TEST)
DEFAULT_RATIOS = (0.7, 0.15, 0.15)
DEFAULT_SEED = 42

# First production-grade dataset target (mission: 200-300 meaningful labels).
MIN_MEANINGFUL_LABELS = 200
TARGET_MEANINGFUL_LABELS = 300
MIN_HARD_CASES = 20
HARD_NEGATIVE_MAX_RANK = 20  # rated highly by the system, rejected by the human


class InvalidSplit(ValueError):
    pass


def validate_ratios(ratios: Iterable[float]) -> tuple[float, float, float]:
    values = tuple(float(r) for r in ratios)
    if len(values) != 3 or any(r < 0 for r in values) or abs(sum(values) - 1.0) > 1e-6:
        raise InvalidSplit("ratios must be three non-negative numbers summing to 1")
    if values[0] <= 0 or values[1] + values[2] <= 0:
        raise InvalidSplit("a split needs a train share and at least one evaluation share")
    return (values[0], values[1], values[2])


def normalize_company(name: str) -> str:
    return re.sub(r"[\W_]+", " ", name.casefold()).strip()


def group_vacancies(
    company_by_vacancy: Mapping[str, str], pairs: Iterable[tuple[str, str]] = ()
) -> dict[str, str]:
    """vacancy id -> group id (the smallest member id, so stable and order-independent)."""
    parent: dict[str, str] = {v: v for v in company_by_vacancy}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)

    by_company: dict[str, str] = {}
    for vacancy, company in sorted(company_by_vacancy.items()):
        key = normalize_company(company)
        if not key:
            continue  # an unknown company must not glue unrelated vacancies together
        if key in by_company:
            union(vacancy, by_company[key])
        else:
            by_company[key] = vacancy
    for a, b in pairs:
        if a in parent and b in parent:
            union(a, b)
    return {vacancy: find(vacancy) for vacancy in parent}


def hashed_fold(group_id: str, *, seed: int, ratios: tuple[float, float, float]) -> str:
    digest = hashlib.sha256(f"{seed}:{group_id}".encode()).digest()
    position = int.from_bytes(digest[:8], "big") / 2**64
    train_share, validation_share, _ = ratios
    if position < train_share:
        return TRAIN
    if position < train_share + validation_share:
        return VALIDATION
    return TEST


def assign_folds(
    group_by_vacancy: Mapping[str, str],
    *,
    seed: int = DEFAULT_SEED,
    ratios: tuple[float, float, float] = DEFAULT_RATIOS,
    frozen_eval: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """vacancy id -> fold. ``frozen_eval`` maps vacancy id -> validation/test (see module doc)."""
    frozen_eval = frozen_eval or {}
    pinned: dict[str, str] = {}
    for vacancy, group in group_by_vacancy.items():
        fold = frozen_eval.get(vacancy)
        if fold is None:
            continue
        if fold not in EVAL_FOLDS:
            raise InvalidSplit(f"frozen fold must be one of {EVAL_FOLDS}, got {fold!r}")
        if pinned.get(group) != TEST:  # test outranks validation inside one group
            pinned[group] = fold
    folds: dict[str, str] = {}
    for vacancy, group in group_by_vacancy.items():
        folds[vacancy] = pinned.get(group) or hashed_fold(group, seed=seed, ratios=ratios)
    return folds


def eval_vacancies(folds: Mapping[str, str]) -> dict[str, str]:
    """The vacancy -> fold entries a freeze must record."""
    return {v: f for v, f in sorted(folds.items()) if f in EVAL_FOLDS}


# ── coverage report ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class LabelObservation:
    """One exported label, reduced to what coverage rules need."""

    kind: str  # pointwise | pair
    label: str  # pointwise label, or "decisive" for a pair
    resume_id: str
    vacancy_ids: tuple[str, ...]
    company: str = ""
    sampling_reason: str | None = None
    current_rank: int | None = None
    fold: str | None = None


@dataclass(slots=True)
class CoverageReport:
    meaningful_labels: int
    pointwise: int
    pairs: int
    by_label: dict[str, int]
    by_fold: dict[str, int]
    class_balance: dict[str, float]
    hard_negatives: int
    model_disagreement: int
    resumes: int
    vacancies: int
    companies: int
    max_company_share: float
    warnings: list[str] = field(default_factory=list)
    target_min: int = MIN_MEANINGFUL_LABELS
    target: int = TARGET_MEANINGFUL_LABELS

    @property
    def labels_to_go(self) -> int:
        return max(0, self.target_min - self.meaningful_labels)

    @property
    def ready(self) -> bool:
        return not self.warnings and self.meaningful_labels >= self.target_min


def coverage_report(
    observations: Iterable[LabelObservation], *, frozen: bool, undecided_pairs: int = 0
) -> CoverageReport:
    items = list(observations)
    pointwise = [o for o in items if o.kind == "pointwise"]
    pairs = [o for o in items if o.kind == "pair"]
    by_label: dict[str, int] = {}
    for o in pointwise:
        by_label[o.label] = by_label.get(o.label, 0) + 1
    by_fold: dict[str, int] = {}
    for o in items:
        if o.fold:
            by_fold[o.fold] = by_fold.get(o.fold, 0) + 1
    total_pointwise = len(pointwise)
    balance = (
        {label: round(count / total_pointwise, 4) for label, count in sorted(by_label.items())}
        if total_pointwise
        else {}
    )

    hard_negatives = sum(
        1
        for o in pointwise
        if o.label == "not_relevant"
        and o.current_rank is not None
        and o.current_rank <= HARD_NEGATIVE_MAX_RANK
    )
    disagreement = sum(1 for o in items if o.sampling_reason == "rank_disagreement")

    vacancies = {v for o in items for v in o.vacancy_ids}
    companies: dict[str, int] = {}
    for o in items:
        key = normalize_company(o.company)
        if key:
            companies[key] = companies.get(key, 0) + 1
    labelled_companies = sum(companies.values())
    max_share = round(max(companies.values()) / labelled_companies, 4) if companies else 0.0

    meaningful = total_pointwise + len(pairs)
    warnings: list[str] = []
    if meaningful < MIN_MEANINGFUL_LABELS:
        warnings.append(f"only {meaningful} of the {MIN_MEANINGFUL_LABELS} required labels")
    if total_pointwise and not pairs or pairs and not total_pointwise:
        warnings.append("both pointwise and pairwise labels are required")
    if not total_pointwise and not pairs:
        warnings.append("no labels yet")
    if hard_negatives + disagreement < MIN_HARD_CASES:
        warnings.append(
            f"fewer than {MIN_HARD_CASES} hard cases (hard negatives + model disagreement)"
        )
    for label in ("relevant", "not_relevant"):
        if total_pointwise >= 20 and by_label.get(label, 0) / total_pointwise < 0.1:
            warnings.append(f"class {label!r} is under 10% of pointwise labels")
    if max_share > 0.3 and labelled_companies >= 20:
        warnings.append(f"one company holds {max_share:.0%} of the labels")
    if len({o.resume_id for o in items}) < 2 and items:
        warnings.append("labels cover a single resume: evaluation reflects one profile only")
    if not frozen:
        warnings.append("no frozen evaluation split yet")
    if undecided_pairs:
        pass  # informative only: undecided pairs are kept out of `meaningful`

    return CoverageReport(
        meaningful_labels=meaningful,
        pointwise=total_pointwise,
        pairs=len(pairs),
        by_label=by_label,
        by_fold=by_fold,
        class_balance=balance,
        hard_negatives=hard_negatives,
        model_disagreement=disagreement,
        resumes=len({o.resume_id for o in items}),
        vacancies=len(vacancies),
        companies=len(companies),
        max_company_share=max_share,
        warnings=warnings,
    )
