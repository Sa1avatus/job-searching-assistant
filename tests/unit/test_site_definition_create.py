from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.domain.site_access import InvalidSiteAccess
from app.services.recruitment import DuplicateEntityError, EntityNotFoundError
from app.services.site_definitions import InvalidSiteDefinition, create_site_definition
from app.storage.database import Base
from app.storage.tables import SiteDefinitionRow, UserRow


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def _create_user(session: Session, user_id: str = "user-1") -> None:
    session.add(UserRow(id=user_id, display_name="Candidate"))
    session.commit()


def test_create_site_definition_persists_canonical_copied_values(session: Session) -> None:
    _create_user(session)
    rules: dict[str, object] = {"logged_in": {"selectors": ["main", "nav"]}}

    row = create_site_definition(
        session,
        user_id="user-1",
        site_key=" Custom.Site ",
        name="  Custom   Site ",
        login_url="https://BÜCHER.example.:8443/login?next=%2F",
        allowed_hosts=["other.example", "bücher.example", "BÜCHER.example."],
        authorization_rules=rules,
    )
    rules["logged_in"] = False

    assert row.id
    assert row.site_key == "custom.site"
    assert row.name == "Custom Site"
    assert row.login_url == "https://xn--bcher-kva.example:8443/login?next=%2F"
    assert row.allowed_hosts == ["other.example", "xn--bcher-kva.example"]
    assert row.authorization_rules == {"logged_in": {"selectors": ["main", "nav"]}}
    assert row.archived_at is None


def test_create_site_definition_requires_existing_user(session: Session) -> None:
    with pytest.raises(EntityNotFoundError, match="^User not found$"):
        create_site_definition(
            session,
            user_id="missing",
            site_key="site",
            name="Site",
            login_url="https://example.com",
            allowed_hosts=["example.com"],
        )


def test_create_site_definition_rejects_archived_duplicate(session: Session) -> None:
    _create_user(session)
    create_site_definition(
        session,
        user_id="user-1",
        site_key="site",
        name="Site",
        login_url="https://example.com",
        allowed_hosts=["example.com"],
    )

    with pytest.raises(DuplicateEntityError, match="^Site key already exists$"):
        create_site_definition(
            session,
            user_id="user-1",
            site_key="SITE",
            name="Another",
            login_url="https://example.com",
            allowed_hosts=["example.com"],
        )

    assert session.scalar(select(func.count()).select_from(SiteDefinitionRow)) == 1


@pytest.mark.parametrize("site_key", ["", "bad key", "site/one", "я", 123])
def test_create_site_definition_rejects_invalid_site_key(
    session: Session,
    site_key: object,
) -> None:
    _create_user(session)
    with pytest.raises(InvalidSiteDefinition, match="^Site key is invalid$"):
        create_site_definition(
            session,
            user_id="user-1",
            site_key=site_key,  # type: ignore[arg-type]
            name="Site",
            login_url="https://example.com",
            allowed_hosts=["example.com"],
        )


@pytest.mark.parametrize("name", ["", "   ", "x" * 201, 123])
def test_create_site_definition_rejects_invalid_name(
    session: Session,
    name: object,
) -> None:
    _create_user(session)
    with pytest.raises(InvalidSiteDefinition, match="^Site name is invalid$"):
        create_site_definition(
            session,
            user_id="user-1",
            site_key="site",
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
        {"value": float("inf")},
        {"value": [[[[[["too deep"]]]]]]},
        {"values": list(range(200))},
    ],
)
def test_create_site_definition_rejects_invalid_authorization_rules(
    session: Session,
    rules: object,
) -> None:
    _create_user(session)
    with pytest.raises(
        InvalidSiteDefinition,
        match="^Authorization rules are invalid$",
    ):
        create_site_definition(
            session,
            user_id="user-1",
            site_key="site",
            name="Site",
            login_url="https://example.com",
            allowed_hosts=["example.com"],
            authorization_rules=rules,  # type: ignore[arg-type]
        )


def test_create_site_definition_propagates_site_access_error(session: Session) -> None:
    _create_user(session)
    with pytest.raises(InvalidSiteAccess, match="^Login URL host is not allowed$"):
        create_site_definition(
            session,
            user_id="user-1",
            site_key="site",
            name="Site",
            login_url="https://sub.example.com",
            allowed_hosts=["example.com"],
        )
