from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.recruitment import EntityNotFoundError
from app.storage.tables import SiteDefinitionRow, UserRow


def archive_site_definition(
    session: Session,
    *,
    user_id: str,
    site_definition_id: str,
) -> SiteDefinitionRow:
    """Soft-archive one user-owned site definition idempotently."""
    if session.get(UserRow, user_id) is None:
        raise EntityNotFoundError("User not found")
    row = session.scalar(
        select(SiteDefinitionRow).where(
            SiteDefinitionRow.id == site_definition_id,
            SiteDefinitionRow.user_id == user_id,
        )
    )
    if row is None:
        raise EntityNotFoundError("Site definition not found")
    if row.archived_at is not None:
        return row

    row.archived_at = datetime.now(UTC)
    session.commit()
    return row
