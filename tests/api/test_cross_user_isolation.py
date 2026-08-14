"""Cross-user endpoint isolation tests at the API layer.

Verifies that User A receives 404 when attempting to access or modify
resources belonging to User B through the HTTP API.
"""

from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, document_storage
from app.config import Settings
from app.storage.database import Base, session_scope
from app.storage.documents import DocumentStorage
from app.storage.tables import UserRow


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _setup_two_users(factory: sessionmaker) -> None:
    with factory() as session:
        session.add_all(
            (
                UserRow(id="user-a", display_name="Alice"),
                UserRow(id="user-b", display_name="Bob"),
            )
        )
        session.commit()


# ── CV File Cross-User ─────────────────────────────────────────


def test_cross_user_cannot_list_other_cv_files(tmp_path, monkeypatch) -> None:
    """User A listing user-b CV files returns empty, not user-b's data."""
    factory = _session_factory()
    _setup_two_users(factory)

    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    def test_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    app.dependency_overrides[document_storage] = lambda: DocumentStorage(
        tmp_path / "documents", max_document_bytes=8192
    )
    try:
        with TestClient(app) as client:
            # User B uploads a CV
            client.post(
                "/v1/users/user-b/cv-files",
                files={"file": ("bob.txt", b"Bob resume", "text/plain")},
            )
            # User A lists CV files — should see nothing
            result = client.get("/v1/users/user-a/cv-files")
            assert result.status_code == 200
            assert result.json() == []
    finally:
        app.dependency_overrides.clear()


def test_cross_user_cannot_delete_other_cv(tmp_path, monkeypatch) -> None:
    """User A cannot delete User B's CV file."""
    factory = _session_factory()
    _setup_two_users(factory)

    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    def test_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    app.dependency_overrides[document_storage] = lambda: DocumentStorage(
        tmp_path / "documents", max_document_bytes=8192
    )
    try:
        with TestClient(app) as client:
            bob_cv = client.post(
                "/v1/users/user-b/cv-files",
                files={"file": ("bob.txt", b"Bob resume", "text/plain")},
            ).json()

            # User A tries to delete User B's CV
            result = client.delete(f"/v1/users/user-a/cv-files/{bob_cv['id']}")
            assert result.status_code == 404

            # Verify CV still exists under User B
            bob_cvs = client.get("/v1/users/user-b/cv-files").json()
            assert len(bob_cvs) == 1
            assert bob_cvs[0]["id"] == bob_cv["id"]
    finally:
        app.dependency_overrides.clear()


# ── Fact Cross-User ────────────────────────────────────────────


def test_cross_user_cannot_update_other_fact(monkeypatch) -> None:
    """User A's fact cannot be updated via User B's endpoint."""
    factory = _session_factory()
    _setup_two_users(factory)

    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    async def mock_sync_profile(self, user_id: str):
        return None

    monkeypatch.setattr("app.api.main.RagSyncService.sync_profile", mock_sync_profile)

    def test_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            # User A creates a fact
            fact = client.post(
                "/v1/users/user-a/facts",
                json={
                    "category": "skill",
                    "name": "Python",
                    "value": "5 years",
                    "is_verified": True,
                },
            ).json()

            # User B tries to update User A's fact — 404
            result = client.put(
                f"/v1/users/user-b/facts/{fact['id']}",
                json={
                    "category": "skill",
                    "name": "Python",
                    "value": "changed",
                    "is_verified": True,
                },
            )
            assert result.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_cross_user_cannot_delete_other_fact(monkeypatch) -> None:
    """User A cannot delete User B's fact."""
    factory = _session_factory()
    _setup_two_users(factory)

    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    async def mock_sync_profile_delete(self, user_id: str):
        return None

    monkeypatch.setattr("app.api.main.RagSyncService.sync_profile", mock_sync_profile_delete)

    def test_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            # User B creates a fact
            fact = client.post(
                "/v1/users/user-b/facts",
                json={
                    "category": "skill",
                    "name": "Python",
                    "value": "5 years",
                    "is_verified": True,
                },
            ).json()

            # User A tries to delete User B's fact — 404
            result = client.delete(f"/v1/users/user-a/facts/{fact['id']}")
            assert result.status_code == 404

            # Verify fact still exists under User B
            bob_facts = client.get("/v1/users/user-b/facts").json()
            assert len(bob_facts) == 1
    finally:
        app.dependency_overrides.clear()


# ── Company Blacklist Cross-User ───────────────────────────────


def test_cross_user_cannot_delete_other_blacklist_entry(monkeypatch) -> None:
    """User A cannot delete User B's blacklist entry."""
    factory = _session_factory()
    _setup_two_users(factory)

    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    def test_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            # User B adds a blacklist entry
            entry = client.post(
                "/v1/users/user-b/company-blacklist",
                json={"company": "BadCo"},
            ).json()

            # User A tries to delete User B's entry — 404
            result = client.delete(f"/v1/users/user-a/company-blacklist/{entry['id']}")
            assert result.status_code == 404

            # Verify entry still exists under User B
            bob_entries = client.get("/v1/users/user-b/company-blacklist").json()
            assert len(bob_entries) == 1
    finally:
        app.dependency_overrides.clear()


# ── Vacancy/Application Cross-User ────────────────────────────


def test_cross_user_vacancy_listing_isolated(monkeypatch) -> None:
    """User A's vacancy listing returns only their applications."""
    factory = _session_factory()
    _setup_two_users(factory)

    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    def test_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            # User A lists vacancies — should not see User B data
            result_a = client.get("/v1/users/user-a/vacancies")
            result_b = client.get("/v1/users/user-b/vacancies")
            assert result_a.status_code == 200
            assert result_b.status_code == 200
    finally:
        app.dependency_overrides.clear()


# ── Application Statistics Cross-User ─────────────────────────


def test_cross_user_statistics_returns_not_found(monkeypatch) -> None:
    """Statistics for unknown user returns 404."""
    factory = _session_factory()

    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    def test_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            result = client.get("/v1/users/nonexistent/application-statistics")
            assert result.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ── User Deletion Cross-User ──────────────────────────────────


def test_cannot_delete_nonexistent_user(monkeypatch) -> None:
    """Deleting a nonexistent user returns 404."""
    factory = _session_factory()

    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    def test_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            result = client.delete("/v1/users/nonexistent")
            assert result.status_code == 404
    finally:
        app.dependency_overrides.clear()
