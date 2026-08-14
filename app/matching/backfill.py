from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.matching.jobs import (
    BACKFILL_MATCHING_PRIORITY,
    MatchingJobNotReadyError,
    MatchingJobService,
)
from app.matching.vacancy_source import VACANCY_MATCHING_SOURCE_VERSION
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CandidateEvidenceRow,
    EmbeddingRecordRow,
    UserRow,
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


@dataclass(frozen=True, slots=True)
class MatchingBackfillResult:
    user_id: str
    applications_scanned: int
    scheduled: int
    skipped_current: int
    skipped_without_result: int
    skipped_not_ready: int
    failed: int
    failure_codes: dict[str, int]
    next_application_cursor: str | None
    has_more: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


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
                    MatchingJobService(self._session).schedule(
                        application_id,
                        priority=BACKFILL_MATCHING_PRIORITY,
                    )
                    scheduled += 1
                except Exception as error:
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
                select(func.count())
                .select_from(ApplicationRow)
                .where(ApplicationRow.selected_cv_file_id.is_not(None))
            )
            or 0
        )

    def schedule_stale_owner_results(
        self,
        user_id: str,
        *,
        batch_size: int = 50,
        after_application_id: str | None = None,
    ) -> MatchingBackfillResult:
        if batch_size < 1 or batch_size > 100:
            raise ValueError("batch_size must be between 1 and 100")
        if self._session.get(UserRow, user_id) is None:
            raise ValueError("user not found")

        query = (
            select(ApplicationRow)
            .where(ApplicationRow.user_id == user_id)
            .order_by(ApplicationRow.id)
            .limit(batch_size + 1)
        )
        if after_application_id is not None:
            query = query.where(ApplicationRow.id > after_application_id)
        rows = list(self._session.scalars(query).all())
        batch = rows[:batch_size]

        scheduled = 0
        skipped_current = 0
        skipped_without_result = 0
        skipped_not_ready = 0
        failed = 0
        failure_codes: dict[str, int] = {}
        scheduler = MatchingJobService(self._session)
        for application in batch:
            aggregate = self._session.get(ApplicationMatchResultRow, application.id)
            if aggregate is None:
                skipped_without_result += 1
                continue
            source_version = str(aggregate.explanation_json.get("matching_source_version", ""))
            if (
                aggregate.status not in {"stale", "failed"}
                and source_version == VACANCY_MATCHING_SOURCE_VERSION
            ):
                skipped_current += 1
                continue
            try:
                scheduler.schedule(
                    application.id,
                    force=True,
                    priority=BACKFILL_MATCHING_PRIORITY,
                )
                scheduled += 1
            except MatchingJobNotReadyError:
                skipped_not_ready += 1
            except Exception as error:
                failed += 1
                code = type(error).__name__
                failure_codes[code] = failure_codes.get(code, 0) + 1

        return MatchingBackfillResult(
            user_id=user_id,
            applications_scanned=len(batch),
            scheduled=scheduled,
            skipped_current=skipped_current,
            skipped_without_result=skipped_without_result,
            skipped_not_ready=skipped_not_ready,
            failed=failed,
            failure_codes=failure_codes,
            next_application_cursor=(batch[-1].id if batch else after_application_id),
            has_more=len(rows) > batch_size,
        )
