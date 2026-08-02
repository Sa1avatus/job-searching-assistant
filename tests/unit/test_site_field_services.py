from collections.abc import Iterator
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.domain.forms import FormField, FormFieldType
from app.security.autofill_decryption import decrypt_autofill_value
from app.services.recruitment import EntityNotFoundError
from app.services.site_fields import (
    InvalidSiteField,
    save_discovered_site_fields,
    upsert_site_field_mapping,
    upsert_site_value_override,
)
from app.storage.database import Base
from app.storage.tables import (
    SiteDefinitionRow,
    SiteFieldMappingRow,
    SiteFieldRow,
    SiteValueOverrideRow,
    UserRow,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def _site(session: Session, *, user_id: str = "user-1") -> SiteDefinitionRow:
    session.add(UserRow(id=user_id, display_name=user_id))
    site = SiteDefinitionRow(
        user_id=user_id,
        site_key=f"site-{user_id}",
        name="Site",
        login_url="https://example.com/login",
        allowed_hosts=["example.com"],
        authorization_rules={},
    )
    session.add(site)
    session.commit()
    return site


def _field() -> FormField:
    return FormField(
        field_id="email",
        label="Email",
        field_type=FormFieldType.TEXT,
        is_required=True,
        semantic_category="email",
        source_locator="label:Email",
        locator_candidates=("label:Email", "id:email", "name:email"),
    )


def test_save_discovered_fields_upserts_and_preserves_identity(session: Session) -> None:
    site = _site(session)

    first = save_discovered_site_fields(
        session,
        user_id="user-1",
        site_definition_id=site.id,
        fields=(_field(),),
    )[0]
    updated_field = replace(_field(), label="Work email")
    second = save_discovered_site_fields(
        session,
        user_id="user-1",
        site_definition_id=site.id,
        fields=(updated_field,),
    )[0]

    assert second.id == first.id
    assert second.label == "Work email"
    assert second.selector_candidates == [
        {"kind": "label", "value": "Email"},
        {"kind": "id", "value": "email"},
        {"kind": "name", "value": "email"},
    ]
    assert session.scalar(select(func.count()).select_from(SiteFieldRow)) == 1


def test_mapping_upsert_validates_key_and_transformation(session: Session) -> None:
    site = _site(session)
    field = save_discovered_site_fields(
        session,
        user_id="user-1",
        site_definition_id=site.id,
        fields=(_field(),),
    )[0]

    mapping = upsert_site_field_mapping(
        session,
        user_id="user-1",
        site_field_id=field.id,
        value_key="contact.email",
        transformation={"kind": "trim"},
        review_required=True,
    )
    updated = upsert_site_field_mapping(
        session,
        user_id="user-1",
        site_field_id=field.id,
        value_key="custom.work_email",
    )

    assert updated.id == mapping.id
    assert updated.value_key == "custom.work_email"
    assert session.scalar(select(func.count()).select_from(SiteFieldMappingRow)) == 1
    with pytest.raises(InvalidSiteField, match="^Mapping transformation is invalid$"):
        upsert_site_field_mapping(
            session,
            user_id="user-1",
            site_field_id=field.id,
            value_key="contact.email",
            transformation={"kind": "python"},
        )


def test_site_and_field_overrides_are_encrypted_and_separate(session: Session) -> None:
    site = _site(session)
    field = save_discovered_site_fields(
        session,
        user_id="user-1",
        site_definition_id=site.id,
        fields=(_field(),),
    )[0]
    key = Fernet.generate_key().decode()

    site_override = upsert_site_value_override(
        session,
        user_id="user-1",
        site_definition_id=site.id,
        value_key="contact.email",
        serialized_value="site@example.test",
        is_sensitive=False,
        encryption_key=key,
    )
    field_override = upsert_site_value_override(
        session,
        user_id="user-1",
        site_definition_id=site.id,
        site_field_id=field.id,
        value_key="contact.email",
        serialized_value="field@example.test",
        is_sensitive=True,
        encryption_key=key,
    )

    assert site_override.scope_key == "*"
    assert field_override.scope_key == field.id
    assert site_override.encrypted_value != "site@example.test"
    assert decrypt_autofill_value(site_override.encrypted_value, encryption_key=key) == (
        "site@example.test"
    )
    assert session.scalar(select(func.count()).select_from(SiteValueOverrideRow)) == 2


def test_site_field_services_do_not_disclose_cross_user_records(session: Session) -> None:
    first_site = _site(session, user_id="user-1")
    _site(session, user_id="user-2")
    field = save_discovered_site_fields(
        session,
        user_id="user-1",
        site_definition_id=first_site.id,
        fields=(_field(),),
    )[0]

    with pytest.raises(EntityNotFoundError, match="^Site field not found$"):
        upsert_site_field_mapping(
            session,
            user_id="user-2",
            site_field_id=field.id,
            value_key="contact.email",
        )
