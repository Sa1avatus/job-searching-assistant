from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.recruitment import EntityNotFoundError
from app.storage.tables import ApplicationRow, UserRow, VacancyRow

_SYNC_ADAPTERS = frozenset({"headhunter", "linkedin-reference"})
_TERMINAL_STATUSES = frozenset({"submitted", "interview", "rejected", "employer_rejected"})


class ApplicationSubmissionProbe(Protocol):
    async def has_submitted_application(self, url: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ApplicationSyncSummary:
    checked: int
    updated: int
    unchanged: int
    skipped: int
    failed: int


class ApplicationStatusSyncService:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def synchronize(
        self,
        user_id: str,
        probes: dict[str, ApplicationSubmissionProbe],
    ) -> ApplicationSyncSummary:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")
        rows = self._session.execute(
            select(ApplicationRow, VacancyRow)
            .join(VacancyRow, VacancyRow.id == ApplicationRow.vacancy_id)
            .where(
                ApplicationRow.user_id == user_id,
                VacancyRow.adapter_name.in_(_SYNC_ADAPTERS),
            )
            .order_by(ApplicationRow.created_at, ApplicationRow.id)
        ).all()
        checked = updated = unchanged = skipped = failed = 0
        for application, vacancy in rows:
            probe = probes.get(vacancy.adapter_name)
            if application.status in _TERMINAL_STATUSES or probe is None:
                skipped += 1
                continue
            checked += 1
            try:
                is_submitted = await probe.has_submitted_application(vacancy.source_url)
            except Exception:  # noqa: BLE001 - one inaccessible vacancy must not abort the batch
                failed += 1
                continue
            if not is_submitted:
                unchanged += 1
                continue
            application.status = "submitted"
            updated += 1
        self._session.commit()
        return ApplicationSyncSummary(
            checked=checked,
            updated=updated,
            unchanged=unchanged,
            skipped=skipped,
            failed=failed,
        )
