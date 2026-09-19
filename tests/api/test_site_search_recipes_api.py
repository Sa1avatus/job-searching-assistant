from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.config import get_settings
from app.services.browser_worker_client import (
    BrowserSearchHit,
    BrowserWorkerClient,
    BrowserWorkerRejected,
)
from app.services.site_search_recipes import active_recipe
from app.storage.database import Base, session_scope
from app.storage.tables import SiteDefinitionRow, UserRow

RECIPE = {
    "url_template": "https://careers.example.com/search?q={query}",
    "card_selector": "li.job-card",
    "link_selector": "a.job-link",
    "title_selector": "h2",
    "company_selector": "span.company-name",
}
HIT = BrowserSearchHit(
    source_url="https://careers.example.com/jobs/1", title="Engineer", company="Acme"
)


@pytest.fixture
def client_and_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, sessionmaker]]:
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        setup.add(UserRow(id="user-1", display_name="Candidate"))
        setup.add(
            SiteDefinitionRow(
                id="site-1",
                user_id="user-1",
                site_key="careers",
                name="Careers",
                login_url="https://careers.example.com/login",
                allowed_hosts=["careers.example.com"],
            )
        )
        setup.commit()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = override
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(session_scope, None)


BASE = "/v1/users/user-1/site-definitions/site-1/search-recipe"


def _stub_search(monkeypatch: pytest.MonkeyPatch, hits: list[BrowserSearchHit]) -> None:
    async def fake(self: BrowserWorkerClient, **_kwargs: object) -> list[BrowserSearchHit]:
        return hits

    monkeypatch.setattr(BrowserWorkerClient, "custom_search", fake)


def test_recipe_must_be_verified_before_activation_and_can_roll_back(
    client_and_factory: tuple[TestClient, sessionmaker], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, factory = client_and_factory

    draft = client.put(f"{BASE}/draft", json=RECIPE)
    assert draft.status_code == 200
    assert draft.json()["status"] == "draft"
    assert draft.json()["verified_at"] is None
    assert client.post(f"{BASE}/1/activate").status_code == 409

    _stub_search(monkeypatch, [])
    empty = client.post(f"{BASE}/1/test", json={"query": "python"})
    assert empty.json()["verified_at"] is None
    assert client.post(f"{BASE}/1/activate").status_code == 409

    _stub_search(monkeypatch, [HIT])
    tested = client.post(f"{BASE}/1/test", json={"query": "python"})
    assert tested.json()["preview"] == [
        {"source_url": HIT.source_url, "title": "Engineer", "company": "Acme"}
    ]
    assert client.post(f"{BASE}/1/activate").json()["status"] == "active"

    second = client.put(f"{BASE}/draft", json={**RECIPE, "card_selector": "article.card"})
    assert second.json()["version"] == 2
    client.post(f"{BASE}/2/test", json={"query": "python"})
    assert client.post(f"{BASE}/2/activate").json()["status"] == "active"
    statuses = {version["version"]: version["status"] for version in client.get(BASE).json()}
    assert statuses == {1: "archived", 2: "active"}

    assert client.post(f"{BASE}/1/activate").json()["status"] == "active"  # rollback
    with factory() as session:
        site = session.get(SiteDefinitionRow, "site-1")
        assert active_recipe(session, site).card_selector == "li.job-card"


@pytest.mark.parametrize(
    "override",
    [
        {"url_template": "https://evil.example.org/search?q={query}"},
        {"url_template": "https://careers.example.com/search"},
        {"card_selector": "xpath=//li"},
    ],
)
def test_unsafe_recipes_are_rejected(
    client_and_factory: tuple[TestClient, sessionmaker], override: dict[str, str]
) -> None:
    client, _factory = client_and_factory

    response = client.put(f"{BASE}/draft", json={**RECIPE, **override})

    assert response.status_code == 422
    assert client.get(BASE).json() == []


def test_learn_saves_a_verified_draft_and_reports_worker_refusals(
    client_and_factory: tuple[TestClient, sessionmaker], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _factory = client_and_factory

    async def learned(self: BrowserWorkerClient, **_kwargs: object) -> dict[str, object]:
        return {
            "recipe": RECIPE,
            "card_count": 4,
            "preview": [{"source_url": HIT.source_url, "title": "Engineer", "company": "Acme"}],
        }

    monkeypatch.setattr(BrowserWorkerClient, "custom_learn", learned)
    body = {"results_url": "https://careers.example.com/search?q=python", "query": "python"}
    response = client.post(f"{BASE}/learn", json=body)

    assert response.status_code == 201
    assert response.json()["verified_at"] is not None
    assert response.json()["learned_from_url"] == body["results_url"]
    assert client.post(f"{BASE}/1/activate").status_code == 200

    async def refused(self: BrowserWorkerClient, **_kwargs: object) -> dict[str, object]:
        raise BrowserWorkerRejected("Запрос не найден в адресе страницы")

    monkeypatch.setattr(BrowserWorkerClient, "custom_learn", refused)
    failed = client.post(f"{BASE}/learn", json=body)
    assert failed.status_code == 422
    assert "адресе" in failed.json()["detail"]


def test_other_users_cannot_reach_a_site(
    client_and_factory: tuple[TestClient, sessionmaker],
) -> None:
    client, _factory = client_and_factory

    response = client.get("/v1/users/user-2/site-definitions/site-1/search-recipe")

    assert response.status_code == 404
