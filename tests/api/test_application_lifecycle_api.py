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


def test_email_review_items_expose_class_and_explanations(env) -> None:
    client, factory = env
    with factory() as db:
        from app.services.application_email_events import ApplicationEmailEventService

        ApplicationEmailEventService(db).ingest(
            "u1", "Application update", "Unfortunately, we will not be moving forward."
        )

    items = client.get("/v1/users/u1/email-review").json()

    assert len(items) == 1
    item = items[0]
    assert item["email_class"] == "rejection"
    assert item["match_method"] == "none" and item["match_reason"]
    assert "not linked confidently" in item["review_reason"]


def test_crm_endpoints_are_owner_scoped_and_validate_group_by(env) -> None:
    client, _ = env
    _status(client, "submitted")

    funnel = client.get("/v1/users/u1/crm/funnel?group_by=source").json()
    assert funnel["totals"]["submitted"] == 1 and funnel["groups"][0]["group"]
    assert "Rates use only mature submissions" in funnel["notes"][0]
    assert client.get("/v1/users/u1/crm/funnel?group_by=salary").status_code == 422
    assert client.get("/v1/users/nobody/crm/funnel").status_code == 404
    assert client.get("/v1/users/u1/crm/insights").json()["findings"] == []

    journey = client.get("/v1/users/u1/crm/applications/a1/journey").json()
    assert [s["stage"] for s in journey["stages"] if s["reached"]] == ["saved", "submitted"]
    assert client.get("/v1/users/u1/crm/applications/nope/journey").status_code == 404


def test_strategy_endpoints_require_evidence_and_are_owner_scoped(env) -> None:
    client, _ = env

    generated = client.post("/v1/users/u1/strategy/recommendations/generate")
    assert generated.status_code == 200
    body = generated.json()
    assert body["created"] == [] and body["no_recommendation_because"]
    assert client.get("/v1/users/u1/strategy/recommendations").json() == []
    assert client.post("/v1/users/nobody/strategy/recommendations/generate").status_code == 404
    decision = client.post(
        "/v1/users/u1/strategy/recommendations/missing/decision", json={"decision": "accept"}
    )
    assert decision.status_code == 404
    bad = client.post(
        "/v1/users/u1/strategy/recommendations/missing/decision", json={"decision": "maybe"}
    )
    assert bad.status_code == 422
    assert client.get("/v1/users/u1/strategy/recommendations/missing/followup").status_code == 404
