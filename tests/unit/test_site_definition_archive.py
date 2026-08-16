from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.services.recruitment import EntityNotFoundError
from app.services.site_definition_archive import archive_site_definition
from app.storage.database import Base
from app.storage.tables import SiteDefinitionRow, UserRow


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def _add_site(session: Session, *, user_id: str) -> SiteDefinitionRow:
    session.add(UserRow(id=user_id, display_name=user_id))
    row = SiteDefinitionRow(
        user_id=user_id,
        site_key=f"site-{user_id}",
        name="Site",
        login_url="https://example.com/",
        allowed_hosts=["example.com"],
        authorization_rules={},
    )
    session.add(row)
    session.commit()
    return row


def test_archive_site_definition_sets_timestamp_without_deleting(session: Session) -> None:
    row = _add_site(session, user_id="user-1")

    result = archive_site_definition(
        session,
        user_id="user-1",
        site_definition_id=row.id,
    )

    assert result is row
    assert row.archived_at is not None
    assert session.scalar(select(func.count()).select_from(SiteDefinitionRow)) == 1


def test_archive_site_definition_is_idempotent(session: Session) -> None:
    row = _add_site(session, user_id="user-1")
    archive_site_definition(
        session,
        user_id="user-1",
        site_definition_id=row.id,
    )
    original_timestamp = row.archived_at

    result = archive_site_definition(
        session,
        user_id="user-1",
        site_definition_id=row.id,
    )

    assert result is row
    assert row.archived_at == original_timestamp


def test_archive_site_definition_does_not_disclose_cross_user_row(
    session: Session,
) -> None:
    _add_site(session, user_id="user-1")
    other = _add_site(session, user_id="user-2")

    with pytest.raises(EntityNotFoundError, match="^Site definition not found$"):
        archive_site_definition(
            session,
            user_id="user-1",
            site_definition_id=other.id,
        )


def test_archive_site_definition_requires_existing_user(session: Session) -> None:
    with pytest.raises(EntityNotFoundError, match="^User not found$"):
        archive_site_definition(
            session,
            user_id="missing",
            site_definition_id="missing",
        )
