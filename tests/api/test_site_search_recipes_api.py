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


RECORDED_RECIPE = {
    "url_template": "",
    "card_selector": "li.job-card",
    "reach_steps": [
        {
            "action_type": "navigate",
            "parameters": {"url": "https://careers.example.com/"},
        },
        {
            "action_type": "fill",
            "selector_candidates": [{"kind": "id", "value": "q"}],
            "parameters": {"value_key": "query"},
        },
        {
            "action_type": "click",
            "selector_candidates": [{"kind": "id", "value": "go"}],
        },
    ],
}


def test_draft_accepts_a_recorded_scenario_instead_of_a_url_template(
    client_and_factory: tuple[TestClient, sessionmaker],
) -> None:
    client, _factory = client_and_factory

    draft = client.put(f"{BASE}/draft", json=RECORDED_RECIPE)

    assert draft.status_code == 200
    assert draft.json()["recipe"]["url_template"] == ""
    assert [step["action_type"] for step in draft.json()["recipe"]["reach_steps"]] == [
        "navigate",
        "fill",
        "click",
    ]


def test_draft_rejects_a_recorded_scenario_with_no_query_field(
    client_and_factory: tuple[TestClient, sessionmaker],
) -> None:
    client, _factory = client_and_factory

    response = client.put(
        f"{BASE}/draft",
        json={
            "url_template": "",
            "card_selector": "li.job-card",
            "reach_steps": [RECORDED_RECIPE["reach_steps"][0]],
        },
    )

    assert response.status_code == 422


def test_record_start_stop_cancel_proxy_to_the_browser_worker(
    client_and_factory: tuple[TestClient, sessionmaker], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _factory = client_and_factory
    calls: list[tuple[str, dict[str, object]]] = []

    async def start(self: BrowserWorkerClient, **kwargs: object) -> None:
        calls.append(("start", kwargs))

    async def stop(self: BrowserWorkerClient, **kwargs: object) -> dict[str, object]:
        calls.append(("stop", kwargs))
        return {
            "start_url": "https://careers.example.com/",
            "final_url": "https://careers.example.com/search?q=python",
            "actions": [
                {
                    "kind": "fill",
                    "tag": "input",
                    "element_type": "text",
                    "element_id": "q",
                    "name": "",
                    "role": "",
                    "aria_label": "",
                    "test_id": "",
                    "placeholder": "",
                    "label_text": "",
                    "text": "input#q",
                    "value_preview": "python",
                    "value_length": 6,
                    "selector_candidates": [{"kind": "id", "value": "q"}],
                }
            ],
            "learned_recipe": {
                "card_selector": "li.job-card",
                "link_selector": "",
                "title_selector": "",
                "company_selector": "",
                "card_count": 4,
            },
        }

    async def cancel(self: BrowserWorkerClient, **kwargs: object) -> None:
        calls.append(("cancel", kwargs))

    monkeypatch.setattr(BrowserWorkerClient, "custom_record_start", start)
    monkeypatch.setattr(BrowserWorkerClient, "custom_record_stop", stop)
    monkeypatch.setattr(BrowserWorkerClient, "custom_record_cancel", cancel)

    started = client.post(
        f"{BASE}/record/start", json={"start_url": "https://careers.example.com/"}
    )
    assert started.status_code == 202
    assert calls[0] == (
        "start",
        {
            "user_id": "user-1",
            "site": {"site_key": "careers", "allowed_hosts": ["careers.example.com"]},
            "start_url": "https://careers.example.com/",
        },
    )

    stopped = client.post(f"{BASE}/record/stop")
    assert stopped.status_code == 200
    body = stopped.json()
    assert body["final_url"] == "https://careers.example.com/search?q=python"
    assert body["actions"][0]["value_preview"] == "python"
    assert body["learned_recipe"]["card_count"] == 4

    cancelled = client.post(f"{BASE}/record/cancel")
    assert cancelled.status_code == 200
    assert calls[-1][0] == "cancel"


def test_record_start_surfaces_a_worker_refusal(
    client_and_factory: tuple[TestClient, sessionmaker], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _factory = client_and_factory

    async def refused(self: BrowserWorkerClient, **_kwargs: object) -> None:
        raise BrowserWorkerRejected("Запись уже идёт")

    monkeypatch.setattr(BrowserWorkerClient, "custom_record_start", refused)

    response = client.post(
        f"{BASE}/record/start", json={"start_url": "https://careers.example.com/"}
    )

    assert response.status_code == 422
    assert "идёт" in response.json()["detail"]
