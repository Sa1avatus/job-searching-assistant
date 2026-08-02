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


def test_site_definition_crud_and_dynamic_session_status(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_BROWSER_STATE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_ARTIFACT_DIRECTORY", str(tmp_path))
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
            created = client.post(
                "/v1/users/user-1/site-definitions",
                json={
                    "site_key": "custom-careers",
                    "name": "Custom Careers",
                    "login_url": "https://careers.example.com/login",
                    "allowed_hosts": ["careers.example.com"],
                    "authorization_rules": {"login_path_markers": ["/login"]},
                },
            )

            assert created.status_code == 201
            created_body = created.json()
            assert created_body["site_key"] == "custom-careers"
            assert created_body["login_url"] == "https://careers.example.com/login"
            assert created_body["is_archived"] is False

            listed = client.get("/v1/users/user-1/site-definitions")
            assert listed.status_code == 200
            assert [item["site_key"] for item in listed.json()] == ["custom-careers"]

            sessions = client.get("/v1/users/user-1/browser-sessions")
            assert sessions.status_code == 200
            assert [item["site_key"] for item in sessions.json()] == [
                "headhunter",
                "linkedin",
                "custom-careers",
            ]
            custom_status = sessions.json()[2]
            assert custom_status["site_name"] == "Custom Careers"
            assert custom_status["is_custom"] is True

            updated = client.put(
                f"/v1/users/user-1/site-definitions/{created_body['id']}",
                json={
                    "name": "Updated Careers",
                    "login_url": "https://jobs.example.com/sign-in",
                    "allowed_hosts": ["jobs.example.com"],
                    "authorization_rules": {"login_path_markers": ["/sign-in"]},
                },
            )
            assert updated.status_code == 200
            assert updated.json()["name"] == "Updated Careers"
            assert updated.json()["site_key"] == "custom-careers"

            archived = client.post(
                f"/v1/users/user-1/site-definitions/{created_body['id']}/archive"
            )
            assert archived.status_code == 200
            assert archived.json()["is_archived"] is True

            assert client.get("/v1/users/user-1/site-definitions").json() == []
            archived_list = client.get(
                "/v1/users/user-1/site-definitions?include_archived=true"
            )
            assert archived_list.json()[0]["is_archived"] is True
    finally:
        app.dependency_overrides.pop(session_scope, None)
        get_settings.cache_clear()
        engine.dispose()


def test_site_definition_api_rejects_cross_user_and_invalid_hosts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_BROWSER_STATE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_ARTIFACT_DIRECTORY", str(tmp_path))
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
            reserved = client.post(
                "/v1/users/user-1/site-definitions",
                json={
                    "site_key": "LinkedIn",
                    "name": "Conflicting",
                    "login_url": "https://example.com/login",
                    "allowed_hosts": ["example.com"],
                },
            )
            assert reserved.status_code == 409
            assert reserved.json()["detail"] == "Site key is reserved"

            invalid = client.post(
                "/v1/users/user-1/site-definitions",
                json={
                    "site_key": "invalid",
                    "name": "Invalid",
                    "login_url": "https://sub.example.com/login",
                    "allowed_hosts": ["example.com"],
                },
            )
            assert invalid.status_code == 422
            assert invalid.json()["detail"] == "Login URL host is not allowed"

            created = client.post(
                "/v1/users/user-1/site-definitions",
                json={
                    "site_key": "owned",
                    "name": "Owned",
                    "login_url": "https://example.com/login",
                    "allowed_hosts": ["example.com"],
                },
            ).json()
            cross_user = client.put(
                f"/v1/users/user-2/site-definitions/{created['id']}",
                json={
                    "name": "Changed",
                    "login_url": "https://example.com/login",
                    "allowed_hosts": ["example.com"],
                },
            )
            assert cross_user.status_code == 404
            assert cross_user.json()["detail"] == "Site definition not found"
    finally:
        app.dependency_overrides.pop(session_scope, None)
        get_settings.cache_clear()
        engine.dispose()
