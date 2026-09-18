from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.config import Settings
from app.services.recruitment import RecruitmentService
from app.storage.database import Base, session_scope
from app.storage.tables import UserRow


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    with session_factory() as session:
        session.add_all(
            (UserRow(id="user-a", display_name="A"), UserRow(id="user-b", display_name="B"))
        )
        session.commit()
    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = override
    yield session_factory
    app.dependency_overrides.clear()


BODY = {
    "min_salary": 250000,
    "salary_currency": "rub",
    "preferred_locations": ["Москва", "москва", "Berlin"],
    "work_formats": ["remote", "hybrid"],
    "employment_types": ["full_time"],
}


def test_defaults_are_empty_and_put_round_trips(factory: sessionmaker) -> None:
    client = TestClient(app)

    empty = client.get("/v1/users/user-a/preferences").json()
    assert empty["min_salary"] is None and empty["work_formats"] == []

    saved = client.put("/v1/users/user-a/preferences", json=BODY)
    assert saved.status_code == 200
    assert saved.json()["salary_currency"] == "RUB"
    assert saved.json()["preferred_locations"] == ["Москва", "Berlin"]
    assert client.get("/v1/users/user-a/preferences").json() == saved.json()


def test_preferences_are_isolated_between_users(factory: sessionmaker) -> None:
    client = TestClient(app)
    client.put("/v1/users/user-a/preferences", json=BODY)

    other = client.get("/v1/users/user-b/preferences").json()

    assert other["min_salary"] is None and other["preferred_locations"] == []


def test_unknown_user_and_invalid_values(factory: sessionmaker) -> None:
    client = TestClient(app)

    assert client.get("/v1/users/nobody/preferences").status_code == 404
    assert client.put("/v1/users/nobody/preferences", json=BODY).status_code == 404
    bad = client.put("/v1/users/user-a/preferences", json={**BODY, "work_formats": ["underwater"]})
    assert bad.status_code == 422
    assert (
        client.put("/v1/users/user-a/preferences", json={**BODY, "min_salary": -5}).status_code
        == 422
    )


def test_preference_mismatches_become_application_warnings(factory: sessionmaker) -> None:
    client = TestClient(app)
    client.put("/v1/users/user-a/preferences", json=BODY)
    with factory() as session:
        recruitment = RecruitmentService(session)
        vacancy = recruitment.create_vacancy(
            source_url="https://hh.ru/vacancy/1",
            title="Dev",
            company="Acme",
            required_skills=[],
            preferred_skills=[],
            location="Санкт-Петербург",
            salary_text="до 100 000 ₽",
            work_format="office",
            employment_types=["contract"],
        )
        application = recruitment.prepare_application("user-a", vacancy.id)
        warnings = list(application.warnings)
        untouched = recruitment.prepare_application("user-b", vacancy.id)

    for code in ("work_format", "employment_type", "location", "salary"):
        assert any(f"Preference mismatch ({code})" in w for w in warnings), code
    assert not any("Preference mismatch" in w for w in untouched.warnings)
