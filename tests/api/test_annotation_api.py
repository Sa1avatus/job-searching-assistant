from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, required_api_scope
from app.config import Settings
from app.storage.database import Base, session_scope
from app.storage.tables import CvFileRow, UserRow, VacancyRow


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([UserRow(id="alice", display_name="A"), UserRow(id="bob", display_name="B")])
        db.flush()
        for cv_id, owner in (("cv-alice", "alice"), ("cv-bob", "bob")):
            db.add(
                CvFileRow(
                    id=cv_id,
                    user_id=owner,
                    original_filename=f"{cv_id}.pdf",
                    storage_path="/x",
                    content_type="application/pdf",
                    sha256=cv_id,
                    size_bytes=1,
                )
            )
        for vid in ("v1", "v2"):
            db.add(VacancyRow(id=vid, source_url=f"https://e.test/{vid}", title=vid, company="Co"))
        db.commit()
    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))

    def override() -> Iterator[Session]:
        with factory() as db:
            yield db

    app.dependency_overrides[session_scope] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _point(client, user="alice", resume="cv-alice", vacancy="v1", **extra):
    body = {"resume_id": resume, "vacancy_id": vacancy, "label": "relevant", **extra}
    return client.post(f"/v1/annotation/pointwise?user_id={user}", json=body)


def test_pointwise_submit_is_committed_and_visible_in_stats(client: TestClient) -> None:
    response = _point(client, reasons=["strong_match"], confidence="high")

    assert response.status_code == 200 and response.json()["status"] == "accepted"
    stats = client.get("/v1/annotation/stats?user_id=alice").json()
    assert stats["total_pointwise"] == 1 and stats["pointwise_by_label"] == {"relevant": 1}


def test_resubmitting_the_same_pair_does_not_duplicate(client: TestClient) -> None:
    _point(client)
    _point(client, label="not_relevant")

    stats = client.get("/v1/annotation/stats").json()
    assert stats["total_pointwise"] == 1 and stats["pointwise_by_label"] == {"not_relevant": 1}


def test_foreign_resume_is_forbidden(client: TestClient) -> None:
    assert _point(client, user="alice", resume="cv-bob").status_code == 403


def test_domain_validation_errors_carry_code_and_message(client: TestClient) -> None:
    response = _point(client, reasons=["Not A Tag"])

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_reason"
    assert _point(client, vacancy="ghost").json()["detail"]["code"] == "vacancy_not_found"


def test_reverse_pairs_are_one_judgement_and_undecided_pairs_are_not_exported(
    client: TestClient,
) -> None:
    def pair(a: str, b: str, preference: str):
        return client.post(
            "/v1/annotation/pairwise?user_id=alice",
            json={
                "resume_id": "cv-alice",
                "vacancy_a_id": a,
                "vacancy_b_id": b,
                "preference": preference,
                "a_reasons": ["good_fit"],
            },
        )

    assert pair("v1", "v2", "a_better").status_code == 200
    assert pair("v2", "v1", "b_better").status_code == 200  # same judgement, reversed

    export = client.get("/v1/annotation/export?user_id=alice").json()
    assert [(p["winner_id"], p["loser_id"]) for p in export["pairs"]] == [("v1", "v2")]
    assert client.get("/v1/annotation/stats").json()["total_pairwise"] == 1

    assert pair("v1", "v2", "both_equal").status_code == 200  # updates the same row
    export = client.get("/v1/annotation/export?user_id=alice").json()
    assert export["pairs"] == [] and export["undecided_pairs"] == 1


def test_export_is_reproducible(client: TestClient) -> None:
    _point(client)

    first = client.get("/v1/annotation/export").json()["dataset_hash"]
    second = client.get("/v1/annotation/export").json()["dataset_hash"]

    assert first == second


def test_discover_can_be_scoped_to_one_owner(client: TestClient) -> None:
    everyone = client.get("/v1/annotation/discover").json()
    only_alice = client.get("/v1/annotation/discover?user_id=alice").json()

    assert everyone["total_users"] == 2
    assert [u["user_id"] for u in only_alice["users"]] == ["alice"]


def test_annotation_routes_use_the_review_scopes() -> None:
    assert required_api_scope("GET", "/v1/annotation/queue") == "review:read"
    assert required_api_scope("POST", "/v1/annotation/pointwise") == "review:write"
