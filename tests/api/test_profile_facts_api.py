from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.storage.database import Base, session_scope
from app.storage.tables import UserRow


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_profile_facts_can_be_listed_updated_and_deleted_per_user() -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        session.add_all(
            (
                UserRow(id="user-1", display_name="Candidate"),
                UserRow(id="user-2", display_name="Other candidate"),
            )
        )
        session.commit()

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            created = client.post(
                "/v1/users/user-1/facts",
                json={
                    "category": "skill",
                    "name": "Python",
                    "value": "5 years",
                    "is_verified": True,
                },
            )
            fact_id = created.json()["id"]
            listed = client.get("/v1/users/user-1/facts")
            hidden = client.get("/v1/users/user-2/facts")
            updated = client.put(
                f"/v1/users/user-1/facts/{fact_id}",
                json={
                    "category": "skill",
                    "name": "Python",
                    "value": "6 years",
                    "is_verified": True,
                },
            )
            cross_user = client.put(
                f"/v1/users/user-2/facts/{fact_id}",
                json={
                    "category": "skill",
                    "name": "Python",
                    "value": "changed",
                    "is_verified": True,
                },
            )
            deleted = client.delete(f"/v1/users/user-1/facts/{fact_id}")
            empty = client.get("/v1/users/user-1/facts")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert [fact["name"] for fact in listed.json()] == ["Python"]
    assert hidden.json() == []
    assert updated.status_code == 200
    assert updated.json()["value"] == "6 years"
    assert cross_user.status_code == 404
    assert deleted.status_code == 204
    assert empty.json() == []
