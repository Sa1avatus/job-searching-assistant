from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.tables import ApplicationTimelineEventRow


class ApplicationTimelineService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        application_id: str,
        event_type: str,
        *,
        previous_value: str | None = None,
        new_value: str | None = None,
        detail: dict[str, object] | None = None,
        source: str = "system",
    ) -> ApplicationTimelineEventRow:
        event = ApplicationTimelineEventRow(
            application_id=application_id,
            event_type=event_type,
            previous_value=previous_value,
            new_value=new_value,
            detail_json=detail or {},
            source=source,
        )
        self._session.add(event)
        self._session.flush()
        return event

    def record_status_change(
        self,
        application_id: str,
        previous_status: str,
        new_status: str,
        *,
        source: str = "system",
    ) -> ApplicationTimelineEventRow:
        return self.record(
            application_id,
            "status_change",
            previous_value=previous_status,
            new_value=new_status,
            source=source,
        )

    def list_events(
        self,
        application_id: str,
        *,
        limit: int = 100,
    ) -> list[ApplicationTimelineEventRow]:
        return list(
            self._session.scalars(
                select(ApplicationTimelineEventRow)
                .where(ApplicationTimelineEventRow.application_id == application_id)
                .order_by(ApplicationTimelineEventRow.occurred_at.desc())
                .limit(limit)
            ).all()
        )
