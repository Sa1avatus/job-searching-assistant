from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.matching.jobs import MatchingJobService
from app.storage.tables import (
    ApplicationRow,
    CandidateEvidenceRow,
    EmbeddingRecordRow,
)


@dataclass(frozen=True, slots=True)
class BackfillReport:
    selected: int
    scheduled: int
    failures: tuple[tuple[str, str], ...]
    last_application_id: str | None


@dataclass(frozen=True, slots=True)
class IndexConsistencyReport:
    verified_evidence: int
    indexed_evidence: int
    missing_evidence_ids: tuple[str, ...]


class MatchingBackfillService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def schedule_applications(
        self,
        *,
        dry_run: bool,
        limit: int,
        batch_size: int,
        resume_after: str | None = None,
        user_id: str | None = None,
        cv_file_id: str | None = None,
    ) -> BackfillReport:
        if limit <= 0 or batch_size <= 0:
            raise ValueError("limit and batch_size must be positive")
        query = (
            select(ApplicationRow.id)
            .where(ApplicationRow.selected_cv_file_id.is_not(None))
            .order_by(ApplicationRow.id)
            .limit(limit)
        )
        if resume_after is not None:
            query = query.where(ApplicationRow.id > resume_after)
        if user_id is not None:
            query = query.where(ApplicationRow.user_id == user_id)
        if cv_file_id is not None:
            query = query.where(ApplicationRow.selected_cv_file_id == cv_file_id)
        application_ids = tuple(self._session.scalars(query))
        if dry_run:
            return BackfillReport(
                selected=len(application_ids),
                scheduled=0,
                failures=(),
                last_application_id=application_ids[-1] if application_ids else None,
            )

        scheduled = 0
        failures: list[tuple[str, str]] = []
        for start in range(0, len(application_ids), batch_size):
            for application_id in application_ids[start : start + batch_size]:
                try:
                    MatchingJobService(self._session).schedule(application_id)
                    scheduled += 1
                except Exception as error:  # noqa: BLE001 - report and continue bounded backfill
                    self._session.rollback()
                    failures.append((application_id, type(error).__name__))
        return BackfillReport(
            selected=len(application_ids),
            scheduled=scheduled,
            failures=tuple(failures),
            last_application_id=application_ids[-1] if application_ids else None,
        )

    def verify_index_metadata(
        self,
        *,
        user_id: str | None = None,
        cv_file_id: str | None = None,
        failure_limit: int = 100,
    ) -> IndexConsistencyReport:
        if failure_limit <= 0:
            raise ValueError("failure_limit must be positive")
        evidence_query = select(CandidateEvidenceRow.id).where(
            CandidateEvidenceRow.is_verified.is_(True)
        )
        if user_id is not None:
            evidence_query = evidence_query.where(CandidateEvidenceRow.user_id == user_id)
        if cv_file_id is not None:
            evidence_query = evidence_query.where(CandidateEvidenceRow.cv_file_id == cv_file_id)
        evidence_ids = tuple(
            self._session.scalars(evidence_query.order_by(CandidateEvidenceRow.id))
        )
        indexed_ids = set(
            self._session.scalars(
                select(EmbeddingRecordRow.entity_id)
                .where(
                    EmbeddingRecordRow.entity_type == "candidate_evidence",
                    EmbeddingRecordRow.indexed_at.is_not(None),
                    EmbeddingRecordRow.entity_id.in_(evidence_ids),
                )
                .distinct()
            )
        )
        missing = tuple(
            evidence_id for evidence_id in evidence_ids if evidence_id not in indexed_ids
        )
        return IndexConsistencyReport(
            verified_evidence=len(evidence_ids),
            indexed_evidence=len(indexed_ids),
            missing_evidence_ids=missing[:failure_limit],
        )

    def pending_matching_count(self) -> int:
        return int(
            self._session.scalar(
                select(func.count()).select_from(ApplicationRow).where(
                    ApplicationRow.selected_cv_file_id.is_not(None)
                )
            )
            or 0
        )
