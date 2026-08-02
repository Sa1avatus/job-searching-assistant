from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.recruitment import EntityNotFoundError
from app.services.site_definition_read import get_site_definition
from app.storage.database import Base
from app.storage.tables import SiteDefinitionRow, UserRow


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def _add_site(session: Session, *, user_id: str, archived: bool = False) -> SiteDefinitionRow:
    session.add(UserRow(id=user_id, display_name=user_id))
    row = SiteDefinitionRow(
        user_id=user_id,
        site_key=f"site-{user_id}",
        name="Site",
        login_url="https://example.com/",
        allowed_hosts=["example.com"],
        authorization_rules={},
        archived_at=datetime.now(UTC) if archived else None,
    )
    session.add(row)
    session.commit()
    return row


def test_get_site_definition_returns_owned_active_row(session: Session) -> None:
    row = _add_site(session, user_id="user-1")

    assert (
        get_site_definition(
            session,
            user_id="user-1",
            site_definition_id=row.id,
        )
        is row
    )


def test_get_site_definition_can_include_archived_row(session: Session) -> None:
    row = _add_site(session, user_id="user-1", archived=True)

    assert (
        get_site_definition(
            session,
            user_id="user-1",
            site_definition_id=row.id,
            include_archived=True,
        )
        is row
    )


def test_get_site_definition_hides_archived_row_by_default(session: Session) -> None:
    row = _add_site(session, user_id="user-1", archived=True)

    with pytest.raises(EntityNotFoundError, match="^Site definition not found$"):
        get_site_definition(
            session,
            user_id="user-1",
            site_definition_id=row.id,
        )


def test_get_site_definition_does_not_disclose_other_users_row(session: Session) -> None:
    _add_site(session, user_id="user-1")
    other = _add_site(session, user_id="user-2")

    with pytest.raises(EntityNotFoundError, match="^Site definition not found$"):
        get_site_definition(
            session,
            user_id="user-1",
            site_definition_id=other.id,
            include_archived=True,
        )


def test_get_site_definition_requires_existing_user(session: Session) -> None:
    with pytest.raises(EntityNotFoundError, match="^User not found$"):
        get_site_definition(
            session,
            user_id="missing",
            site_definition_id="missing",
        )


def test_get_site_definition_rejects_invalid_archived_filter(session: Session) -> None:
    _add_site(session, user_id="user-1")

    with pytest.raises(ValueError, match="^Archived filter is invalid$"):
        get_site_definition(
            session,
            user_id="user-1",
            site_definition_id="missing",
            include_archived=1,  # type: ignore[arg-type]
        )
