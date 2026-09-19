from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, required_api_scope
from app.config import Settings
from app.services.application_lifecycle import SubmissionLedger
from app.storage.database import Base, session_scope
from app.storage.tables import (
    ApplicationRow,
    ApplicationTimelineEventRow,
    UserRow,
    VacancyRow,
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user = UserRow(id="u1", display_name="U")
        vacancy = VacancyRow(id="v1", source_url="https://hh.ru/vacancy/1", title="T", company="C")
        db.add_all([user, vacancy])
        db.flush()
        db.add(
            ApplicationRow(
                id="a1", user_id="u1", vacancy_id="v1", status="awaiting_review", match_score=1
            )
        )
        db.commit()
    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    def override() -> Iterator[Session]:
        with factory() as db:
            yield db

    app.dependency_overrides[session_scope] = override
    yield TestClient(app), factory
    app.dependency_overrides.clear()


def _status(client, value):
    return client.patch("/v1/applications/a1/status", json={"status": value})


def test_illegal_transition_is_a_structured_409_with_the_allowed_targets(env) -> None:
    client, _ = env
    assert _status(client, "submitted").status_code == 200

    response = _status(client, "awaiting_review")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "illegal_transition"
    assert detail["current"] == "submitted" and detail["requested"] == "awaiting_review"
    assert "interview" in detail["allowed"] and "awaiting_review" not in detail["allowed"]


def test_status_changes_are_audited_and_repeating_one_is_harmless(env) -> None:
    client, factory = env

    assert _status(client, "approved").status_code == 200
    assert _status(client, "approved").status_code == 200

    with factory() as db:
        events = db.query(ApplicationTimelineEventRow).all()
    assert [(e.previous_value, e.new_value, e.source) for e in events] == [
        ("awaiting_review", "approved", "manual_status")
    ]


def test_submission_endpoint_shows_state_attempts_and_allowed_next_statuses(env) -> None:
    client, factory = env
    empty = client.get("/v1/applications/a1/submission").json()
    assert empty["state"] == "none" and empty["attempts"] == []
    assert "approved" in empty["allowed_next_statuses"]

    _status(client, "submitted")
    after = client.get("/v1/applications/a1/submission").json()

    assert after["state"] == "confirmed"
    assert [a["verified_by"] for a in after["attempts"]] == ["manual"]
    assert client.get("/v1/applications/nope/submission").status_code == 404


def test_resolving_an_unknown_attempt(env) -> None:
    client, factory = env
    with factory() as db:
        ledger = SubmissionLedger(db)
        ledger.mark_unknown(
            ledger.begin(db.get(ApplicationRow, "a1"), "headhunter").row, detail="x"
        )
        db.commit()

    not_submitted = client.post("/v1/applications/a1/submission/resolve", json={"submitted": False})
    assert not_submitted.status_code == 200 and not_submitted.json()["state"] == "failed"
    assert client.get("/v1/applications/a1/submission").json()["state"] == "none"

    with factory() as db:
        SubmissionLedger(db).begin(db.get(ApplicationRow, "a1"), "headhunter")
        db.commit()
    submitted = client.post("/v1/applications/a1/submission/resolve", json={"submitted": True})
    assert submitted.status_code == 200
    assert submitted.json() == {"state": "confirmed", "application_status": "submitted"}


def test_resolving_without_an_open_attempt_is_a_409(env) -> None:
    client, _ = env

    response = client.post("/v1/applications/a1/submission/resolve", json={"submitted": True})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_unresolved_submission"
    assert (
        client.post(
            "/v1/applications/nope/submission/resolve", json={"submitted": True}
        ).status_code
        == 404
    )


def test_resolve_uses_the_review_write_scope() -> None:
    assert required_api_scope("POST", "/v1/applications/a1/submission/resolve") == "review:write"
    assert required_api_scope("GET", "/v1/applications/a1/submission") == "review:read"
