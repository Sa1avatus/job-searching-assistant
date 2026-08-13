from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching.jobs import MatchingJobNotReadyError, MatchingJobService
from app.matching.vacancy_source import VACANCY_MATCHING_SOURCE_VERSION
from app.storage.tables import ApplicationMatchResultRow, ApplicationRow, UserRow


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
            source_version = str(
                aggregate.explanation_json.get("matching_source_version", "")
            )
            if (
                aggregate.status not in {"stale", "failed"}
                and source_version == VACANCY_MATCHING_SOURCE_VERSION
            ):
                skipped_current += 1
                continue
            try:
                scheduler.schedule(application.id, force=True)
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
