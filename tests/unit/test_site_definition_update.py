from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.site_access import InvalidSiteAccess
from app.services.recruitment import EntityNotFoundError
from app.services.site_definition_update import (
    InvalidSiteDefinitionUpdate,
    update_site_definition,
)
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
        name="Old name",
        login_url="https://old.example/",
        allowed_hosts=["old.example"],
        authorization_rules={"old": True},
        archived_at=datetime.now(UTC) if archived else None,
    )
    session.add(row)
    session.commit()
    return row


def test_update_site_definition_updates_only_mutable_fields(session: Session) -> None:
    row = _add_site(session, user_id="user-1")
    identity = (row.id, row.user_id, row.site_key, row.created_at, row.archived_at)
    rules: dict[str, object] = {"ready": {"selectors": ["main"]}}

    result = update_site_definition(
        session,
        user_id="user-1",
        site_definition_id=row.id,
        name="  New   name ",
        login_url="https://BÜCHER.example.:8443/login",
        allowed_hosts=["bücher.example"],
        authorization_rules=rules,
    )
    rules["ready"] = False

    assert result is row
    assert (row.id, row.user_id, row.site_key, row.created_at, row.archived_at) == identity
    assert row.name == "New name"
    assert row.login_url == "https://xn--bcher-kva.example:8443/login"
    assert row.allowed_hosts == ["xn--bcher-kva.example"]
    assert row.authorization_rules == {"ready": {"selectors": ["main"]}}


@pytest.mark.parametrize("name", ["", " ", "x" * 201, 123])
def test_update_site_definition_rejects_invalid_name(
    session: Session,
    name: object,
) -> None:
    row = _add_site(session, user_id="user-1")
    with pytest.raises(InvalidSiteDefinitionUpdate, match="^Site name is invalid$"):
        update_site_definition(
            session,
            user_id="user-1",
            site_definition_id=row.id,
            name=name,  # type: ignore[arg-type]
            login_url="https://example.com",
            allowed_hosts=["example.com"],
        )


@pytest.mark.parametrize(
    "rules",
    [
        [],
        {1: "value"},
        {"value": object()},
        {"value": float("nan")},
        {"value": [[[[[["too deep"]]]]]]},
        {"values": list(range(200))},
    ],
)
def test_update_site_definition_rejects_invalid_rules(
    session: Session,
    rules: object,
) -> None:
    row = _add_site(session, user_id="user-1")
    with pytest.raises(
        InvalidSiteDefinitionUpdate,
        match="^Authorization rules are invalid$",
    ):
        update_site_definition(
            session,
            user_id="user-1",
            site_definition_id=row.id,
            name="Site",
            login_url="https://example.com",
            allowed_hosts=["example.com"],
            authorization_rules=rules,  # type: ignore[arg-type]
        )


def test_update_site_definition_propagates_access_error(session: Session) -> None:
    row = _add_site(session, user_id="user-1")
    with pytest.raises(InvalidSiteAccess, match="^Login URL host is not allowed$"):
        update_site_definition(
            session,
            user_id="user-1",
            site_definition_id=row.id,
            name="Site",
            login_url="https://sub.example.com",
            allowed_hosts=["example.com"],
        )


def test_update_site_definition_hides_cross_user_and_archived_rows(session: Session) -> None:
    _add_site(session, user_id="user-1")
    other = _add_site(session, user_id="user-2", archived=True)

    with pytest.raises(EntityNotFoundError, match="^Site definition not found$"):
        update_site_definition(
            session,
            user_id="user-1",
            site_definition_id=other.id,
            name="Site",
            login_url="https://example.com",
            allowed_hosts=["example.com"],
        )
