from collections.abc import Iterator

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.config import get_settings
from app.storage.database import Base, session_scope
from app.storage.tables import UserRow


def test_site_field_mapping_override_and_effective_value_api(monkeypatch) -> None:
    monkeypatch.setenv("APP_BROWSER_STATE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup_session:
        setup_session.add_all(
            [
                UserRow(id="user-1", display_name="One"),
                UserRow(id="user-2", display_name="Two"),
            ]
        )
        setup_session.commit()

    def override_session_scope() -> Iterator[Session]:
        with factory() as database_session:
            yield database_session

    app.dependency_overrides[session_scope] = override_session_scope
    try:
        with TestClient(app) as client:
            site = client.post(
                "/v1/users/user-1/site-definitions",
                json={
                    "site_key": "careers",
                    "name": "Careers",
                    "login_url": "https://careers.example.test/login",
                    "allowed_hosts": ["careers.example.test"],
                },
            ).json()
            discovered = client.post(
                f"/v1/users/user-1/site-definitions/{site['id']}/fields/discovery",
                json={
                    "fields": [
                        {
                            "field_key": "email",
                            "semantic_key": "contact.email",
                            "label": "Email",
                            "field_type": "text",
                            "is_required": True,
                            "selector_candidates": ["label:Email", "name:email"],
                        }
                    ]
                },
            )
            assert discovered.status_code == 200
            field = discovered.json()[0]
            assert field["selector_candidates"] == [
                {"kind": "label", "value": "Email"},
                {"kind": "name", "value": "email"},
            ]

            mapping = client.put(
                f"/v1/users/user-1/site-fields/{field['id']}/mapping",
                json={
                    "value_key": "contact.email",
                    "transformation": {"kind": "lowercase"},
                    "review_required": True,
                },
            )
            assert mapping.status_code == 200

            common = client.post(
                "/v1/users/user-1/autofill-values",
                json={
                    "key": "contact.email",
                    "label": "Email",
                    "value_type": "email",
                    "serialized_value": "COMMON@EXAMPLE.TEST",
                },
            )
            assert common.status_code == 201
            common_effective = client.get(
                f"/v1/users/user-1/site-fields/{field['id']}/effective-value"
            )
            assert common_effective.status_code == 200
            assert common_effective.json()["value"] == "common@example.test"
            assert common_effective.json()["source"] == "global"
            assert common_effective.json()["requires_review"] is True

            site_override = client.put(
                f"/v1/users/user-1/site-definitions/{site['id']}/overrides",
                json={
                    "value_key": "contact.email",
                    "serialized_value": "SITE@EXAMPLE.TEST",
                },
            )
            assert site_override.status_code == 200
            assert "serialized_value" not in site_override.json()
            assert "encrypted_value" not in site_override.json()

            field_override = client.put(
                f"/v1/users/user-1/site-definitions/{site['id']}/overrides",
                json={
                    "site_field_id": field["id"],
                    "value_key": "contact.email",
                    "serialized_value": "FIELD@EXAMPLE.TEST",
                },
            )
            assert field_override.status_code == 200
            effective = client.get(
                f"/v1/users/user-1/site-fields/{field['id']}/effective-value"
            )
            assert effective.status_code == 200
            assert effective.json()["value"] == "field@example.test"
            assert effective.json()["source"] == "site_field_override"

            listed = client.get(
                f"/v1/users/user-1/site-definitions/{site['id']}/fields"
            )
            assert listed.status_code == 200
            assert listed.json()[0]["has_site_override"] is True
            assert listed.json()[0]["has_field_override"] is True

            cross_user = client.get(
                f"/v1/users/user-2/site-fields/{field['id']}/effective-value"
            )
            assert cross_user.status_code == 404
    finally:
        app.dependency_overrides.pop(session_scope, None)
        get_settings.cache_clear()
        engine.dispose()


def test_sensitive_effective_value_requires_explicit_permission(monkeypatch) -> None:
    monkeypatch.setenv("APP_BROWSER_STATE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup_session:
        setup_session.add(UserRow(id="user-1", display_name="Candidate"))
        setup_session.commit()

    def override_session_scope() -> Iterator[Session]:
        with factory() as database_session:
            yield database_session

    app.dependency_overrides[session_scope] = override_session_scope
    try:
        with TestClient(app) as client:
            site = client.post(
                "/v1/users/user-1/site-definitions",
                json={
                    "site_key": "secure-careers",
                    "name": "Secure Careers",
                    "login_url": "https://secure.example.test/login",
                    "allowed_hosts": ["secure.example.test"],
                },
            ).json()
            field = client.post(
                f"/v1/users/user-1/site-definitions/{site['id']}/fields/discovery",
                json={
                    "fields": [
                        {
                            "field_key": "phone",
                            "semantic_key": "contact.phone",
                            "label": "Phone",
                            "field_type": "text",
                            "selector_candidates": ["name:phone"],
                        }
                    ]
                },
            ).json()[0]
            client.put(
                f"/v1/users/user-1/site-fields/{field['id']}/mapping",
                json={"value_key": "contact.phone"},
            )
            client.put(
                f"/v1/users/user-1/site-definitions/{site['id']}/overrides",
                json={
                    "site_field_id": field["id"],
                    "value_key": "contact.phone",
                    "serialized_value": "+1-555-0100",
                    "is_sensitive": True,
                },
            )

            blocked = client.get(
                f"/v1/users/user-1/site-fields/{field['id']}/effective-value"
            )
            allowed = client.get(
                f"/v1/users/user-1/site-fields/{field['id']}/effective-value"
                "?allow_sensitive=true"
            )

            assert blocked.status_code == 403
            assert allowed.status_code == 200
            assert allowed.json()["is_sensitive"] is True
            assert allowed.json()["requires_review"] is True
    finally:
        app.dependency_overrides.pop(session_scope, None)
        get_settings.cache_clear()
        engine.dispose()
