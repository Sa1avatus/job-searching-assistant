"""Import human labels that an older JSA version wrote into the application timeline.

The old annotation UI stored every judgement as an ``application_timeline_events`` row of type
``manual_update`` with ``detail_json.type == 'matching_feedback'`` (``label_source: human``). The
resume those labels were given for may no longer exist, so the caller names the *source* resume id
(as it appears in the events) and the *target* resume the labels are attached to. Labels of any
other source resume are left alone.

Safety properties:
* nothing is written unless ``apply=True`` (default is a dry run that only reports);
* only labels marked ``human`` are imported; the original events are never modified;
* imported rows carry ``source='legacy_timeline'`` so they are recognisable and reversible
  (``rollback``), and keep the original timestamp and confidence;
* pairs go through the same canonicalisation as live submissions and are skipped, not duplicated,
  when a label for that logical judgement already exists (re-running is harmless);
* the target resume must belong to the same user as the labels.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.annotation import (
    CONFIDENCE_LEVELS,
    InvalidAnnotation,
    canonicalize_pair,
    normalize_reasons,
    validate_pointwise_label,
)
from app.storage.tables import (
    AnnotationFeedbackRow,
    ApplicationTimelineEventRow,
    CvFileRow,
    VacancyRow,
)

LEGACY_SOURCE = "legacy_timeline"


@dataclass(slots=True)
class LegacyImportReport:
    source_resume_id: str
    target_resume_id: str
    applied: bool
    found_events: int = 0
    imported: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_resume_id": self.source_resume_id,
            "target_resume_id": self.target_resume_id,
            "applied": self.applied,
            "found_events": self.found_events,
            "imported": dict(self.imported),
            "skipped": dict(self.skipped),
        }


def _timestamp(detail: dict[str, Any], fallback: datetime) -> datetime:
    raw = detail.get("timestamp")
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return fallback
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return fallback


def import_legacy_labels(
    session: Session,
    *,
    source_resume_id: str,
    target_resume_id: str,
    apply: bool = False,
) -> LegacyImportReport:
    """Import the human labels given for ``source_resume_id``, attached to ``target_resume_id``."""
    target = session.get(CvFileRow, target_resume_id)
    if target is None:
        raise LookupError("The target resume does not exist")
    owner = target.user_id
    report = LegacyImportReport(source_resume_id, target_resume_id, applied=apply)

    events = session.scalars(
        select(ApplicationTimelineEventRow)
        .where(ApplicationTimelineEventRow.event_type == "manual_update")
        .order_by(ApplicationTimelineEventRow.occurred_at, ApplicationTimelineEventRow.id)
    ).all()
    known_vacancies = set(session.scalars(select(VacancyRow.id)))
    existing_points = set(
        session.execute(
            select(AnnotationFeedbackRow.vacancy_id).where(
                AnnotationFeedbackRow.user_id == owner,
                AnnotationFeedbackRow.resume_id == target_resume_id,
                AnnotationFeedbackRow.feedback_type == "pointwise",
            )
        ).scalars()
    )
    existing_pairs = set(
        session.execute(
            select(AnnotationFeedbackRow.pair_key).where(
                AnnotationFeedbackRow.user_id == owner,
                AnnotationFeedbackRow.resume_id == target_resume_id,
                AnnotationFeedbackRow.feedback_type == "pairwise",
            )
        ).scalars()
    )

    for event in events:
        detail = event.detail_json or {}
        if detail.get("type") != "matching_feedback" or detail.get("resume_id") != source_resume_id:
            continue
        report.found_events += 1
        if detail.get("user_id") != owner:
            report.skipped["other_user"] += 1
            continue
        if detail.get("label_source") != "human":
            report.skipped["not_human"] += 1
            continue
        stamp = _timestamp(detail, event.occurred_at)
        confidence = detail.get("label_confidence")
        confidence = confidence if confidence in CONFIDENCE_LEVELS else None
        try:
            if detail.get("feedback_type") == "pointwise":
                row = _pointwise_row(
                    detail, owner, target_resume_id, stamp, confidence, known_vacancies
                )
                if row.vacancy_id in existing_points:
                    report.skipped["already_imported"] += 1
                    continue
                existing_points.add(row.vacancy_id)
                kind = f"pointwise:{row.label}"
            elif detail.get("feedback_type") == "pairwise":
                row = _pairwise_row(
                    detail, owner, target_resume_id, stamp, confidence, known_vacancies
                )
                if row.pair_key in existing_pairs:
                    report.skipped["already_imported"] += 1
                    continue
                existing_pairs.add(row.pair_key or "")
                kind = f"pairwise:{row.label}"
            else:
                report.skipped["unknown_type"] += 1
                continue
        except (InvalidAnnotation, KeyError) as error:
            report.skipped[f"invalid:{type(error).__name__}"] += 1
            continue
        except LookupError:
            report.skipped["vacancy_missing"] += 1
            continue
        report.imported[kind] += 1
        if apply:
            session.add(row)
    if apply:
        session.flush()
    return report


def _reasons(detail: dict[str, Any], key: str) -> list[str]:
    try:
        return normalize_reasons(detail.get(key) or [])
    except InvalidAnnotation:
        return []  # a free-text legacy reason must not block the label itself


def _pointwise_row(
    detail: dict[str, Any],
    owner: str,
    resume_id: str,
    stamp: datetime,
    confidence: str | None,
    known_vacancies: set[str],
) -> AnnotationFeedbackRow:
    vacancy_id = detail["vacancy_id"]
    if vacancy_id not in known_vacancies:
        raise LookupError(vacancy_id)
    label = validate_pointwise_label(detail["label"])
    return AnnotationFeedbackRow(
        user_id=owner,
        resume_id=resume_id,
        vacancy_id=vacancy_id,
        feedback_type="pointwise",
        label=label,
        reasons=_reasons(detail, "reasons"),
        comment=detail.get("comment"),
        confidence=confidence,
        annotator_id=owner,
        source=LEGACY_SOURCE,
        created_at=stamp,
        updated_at=stamp,
    )


def _pairwise_row(
    detail: dict[str, Any],
    owner: str,
    resume_id: str,
    stamp: datetime,
    confidence: str | None,
    known_vacancies: set[str],
) -> AnnotationFeedbackRow:
    a, b = detail["vacancy_a_id"], detail["vacancy_b_id"]
    if a not in known_vacancies or b not in known_vacancies:
        raise LookupError(f"{a}:{b}")
    pair = canonicalize_pair(
        a, b, detail["preference"], _reasons(detail, "a_reasons"), _reasons(detail, "b_reasons")
    )
    return AnnotationFeedbackRow(
        user_id=owner,
        resume_id=resume_id,
        vacancy_id=pair.first_id,
        vacancy_a_id=pair.first_id,
        vacancy_b_id=pair.second_id,
        pair_key=pair.pair_key,
        feedback_type="pairwise",
        label=pair.label,
        reasons=[],
        a_reasons=pair.first_reasons,
        b_reasons=pair.second_reasons,
        comment=detail.get("comment"),
        confidence=confidence,
        annotator_id=owner,
        source=LEGACY_SOURCE,
        created_at=stamp,
        updated_at=stamp,
    )


def rollback_legacy_labels(session: Session, *, target_resume_id: str) -> int:
    """Delete exactly the rows this importer wrote for a resume (never live labels)."""
    result = session.execute(
        delete(AnnotationFeedbackRow).where(
            AnnotationFeedbackRow.resume_id == target_resume_id,
            AnnotationFeedbackRow.source == LEGACY_SOURCE,
        )
    )
    session.flush()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
