from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.recruitment import EntityNotFoundError
from app.storage.tables import SiteDefinitionRow, UserRow


def get_site_definition(
    session: Session,
    *,
    user_id: str,
    site_definition_id: str,
    include_archived: bool = False,
) -> SiteDefinitionRow:
    if not isinstance(include_archived, bool):
        raise ValueError("Archived filter is invalid")

    user = session.get(UserRow, user_id)
    if user is None:
        raise EntityNotFoundError("User not found")

    site_definition = session.scalar(
        select(SiteDefinitionRow).where(
            SiteDefinitionRow.id == site_definition_id,
            SiteDefinitionRow.user_id == user_id,
        )
    )
    if site_definition is None or (
        not include_archived and site_definition.archived_at is not None
    ):
        raise EntityNotFoundError("Site definition not found")

    return site_definition
