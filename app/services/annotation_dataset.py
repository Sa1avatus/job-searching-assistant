"""Frozen splits, leakage-safe folds and coverage reports for the human label dataset."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.annotation_dataset import (
    DEFAULT_RATIOS,
    DEFAULT_SEED,
    EVAL_FOLDS,
    FOLDS,
    CoverageReport,
    InvalidSplit,
    LabelObservation,
    assign_folds,
    coverage_report,
    eval_vacancies,
    group_vacancies,
    validate_ratios,
)
from app.matching.cross_encoder.annotation import DatasetExport, export_dataset
from app.storage.tables import AnnotationSplitRow, VacancyRow


class SplitNotFound(LookupError):
    pass


class SplitFrozen(ValueError):
    """A frozen split is immutable: it cannot be re-frozen or reconfigured."""


class SplitExists(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DatasetView:
    export: DatasetExport
    folds: dict[str, str]  # vacancy id -> fold
    split: AnnotationSplitRow | None


def create_split(
    session: Session,
    name: str,
    *,
    seed: int = DEFAULT_SEED,
    ratios: tuple[float, float, float] = DEFAULT_RATIOS,
) -> AnnotationSplitRow:
    clean = name.strip()
    if not clean:
        raise InvalidSplit("A split needs a name")
    validated = validate_ratios(ratios)
    row = AnnotationSplitRow(name=clean, seed=seed, ratios=list(validated))
    try:
        with session.begin_nested():
            session.add(row)
    except IntegrityError as error:
        raise SplitExists(f"Split {clean!r} already exists") from error
    return row


def get_split(session: Session, name: str) -> AnnotationSplitRow:
    row = session.scalar(select(AnnotationSplitRow).where(AnnotationSplitRow.name == name))
    if row is None:
        raise SplitNotFound(f"Split {name!r} not found")
    return row


def _company_by_vacancy(session: Session, vacancy_ids: set[str]) -> dict[str, str]:
    if not vacancy_ids:
        return {}
    rows = session.execute(
        select(VacancyRow.id, VacancyRow.company).where(VacancyRow.id.in_(vacancy_ids))
    ).all()
    return {vacancy_id: company or "" for vacancy_id, company in rows}


def _vacancy_ids(export: DatasetExport) -> tuple[set[str], list[tuple[str, str]]]:
    ids = {row["vacancy_id"] for row in export.pointwise}
    pairs = [(row["winner_id"], row["loser_id"]) for row in export.pairs]
    for winner, loser in pairs:
        ids.update((winner, loser))
    return ids, pairs


def build_view(
    session: Session, *, user_id: str | None = None, split_name: str | None = None
) -> DatasetView:
    """The validated export together with the fold of every vacancy under ``split_name``."""
    export = export_dataset(session, user_id)
    ids, pairs = _vacancy_ids(export)
    split = get_split(session, split_name) if split_name else None
    companies = _company_by_vacancy(session, ids)
    groups = group_vacancies(companies, pairs)
    if split is None:
        return DatasetView(export, {}, None)
    ratios = validate_ratios(split.ratios or DEFAULT_RATIOS)
    folds = assign_folds(
        groups, seed=split.seed, ratios=ratios, frozen_eval=split.eval_vacancies or {}
    )
    return DatasetView(export, folds, split)


def freeze_split(session: Session, name: str) -> AnnotationSplitRow:
    """Record the current validation/test vacancies for good.

    Everything labelled so far is assigned folds; the evaluation part is stored and from then
    on pins its groups to evaluation, so it can never leak into a later training run.
    """
    split = get_split(session, name)
    if split.frozen_at is not None:
        raise SplitFrozen(f"Split {name!r} is already frozen")
    view = build_view(session, split_name=name)
    if not view.export.pointwise and not view.export.pairs:
        raise InvalidSplit("Nothing to freeze: there are no valid labels yet")
    split.eval_vacancies = eval_vacancies(view.folds)
    split.dataset_hash = view.export.dataset_hash
    split.label_counts = {
        "pointwise": len(view.export.pointwise),
        "pairs": len(view.export.pairs),
        **{fold: sum(1 for f in view.folds.values() if f == fold) for fold in FOLDS},
    }
    split.frozen_at = datetime.now(UTC)
    session.flush()
    return split


def export_fold(
    session: Session,
    *,
    split_name: str,
    fold: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Export rows tagged with their fold; ``fold`` keeps only that fold's rows."""
    if fold is not None and fold not in FOLDS:
        raise InvalidSplit(f"fold must be one of {', '.join(FOLDS)}")
    view = build_view(session, user_id=user_id, split_name=split_name)
    pointwise = [
        {**row, "fold": view.folds[row["vacancy_id"]]}
        for row in view.export.pointwise
        if row["vacancy_id"] in view.folds
    ]
    pairs = [
        {**row, "fold": view.folds[row["winner_id"]]}
        for row in view.export.pairs
        if row["winner_id"] in view.folds
    ]
    if fold is not None:
        pointwise = [row for row in pointwise if row["fold"] == fold]
        pairs = [row for row in pairs if row["fold"] == fold]
    assert view.split is not None
    return {
        "split": view.split.name,
        "frozen": view.split.frozen_at is not None,
        "dataset_hash": view.export.dataset_hash,
        "fold": fold,
        "pointwise": pointwise,
        "pairs": pairs,
        "issues": [issue.model_dump() for issue in view.export.issues],
    }


def dataset_report(
    session: Session, *, user_id: str | None = None, split_name: str | None = None
) -> dict[str, Any]:
    """Coverage, class balance, hard cases, diversity and readiness of the label dataset."""
    view = build_view(session, user_id=user_id, split_name=split_name)
    ids, _ = _vacancy_ids(view.export)
    companies = _company_by_vacancy(session, ids)
    observations: list[LabelObservation] = []
    for row in view.export.pointwise:
        observations.append(
            LabelObservation(
                "pointwise",
                row["label"],
                row["resume_id"],
                (row["vacancy_id"],),
                companies.get(row["vacancy_id"], ""),
                row.get("sampling_reason"),
                row.get("current_rank"),
                view.folds.get(row["vacancy_id"]),
            )
        )
    for row in view.export.pairs:
        observations.append(
            LabelObservation(
                "pair",
                "decisive",
                row["resume_id"],
                (row["winner_id"], row["loser_id"]),
                companies.get(row["winner_id"], ""),
                row.get("sampling_reason"),
                None,
                view.folds.get(row["winner_id"]),
            )
        )
    frozen = view.split is not None and view.split.frozen_at is not None
    report: CoverageReport = coverage_report(
        observations, frozen=frozen, undecided_pairs=view.export.undecided_pairs
    )
    payload = asdict(report)
    payload.update(
        ready=report.ready,
        labels_to_go=report.labels_to_go,
        undecided_pairs=view.export.undecided_pairs,
        dataset_hash=view.export.dataset_hash,
        export_issues=len(view.export.issues),
        split=(
            {
                "name": view.split.name,
                "frozen": frozen,
                "eval_vacancies": len(view.split.eval_vacancies or {}),
                "eval_folds": list(EVAL_FOLDS),
            }
            if view.split is not None
            else None
        ),
    )
    return payload
