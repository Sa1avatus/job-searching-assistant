from collections.abc import Iterator
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.browser.session_probe import BrowserSessionProbe
from app.config import get_settings
from app.storage.database import Base, session_scope
from app.storage.tables import BrowserSessionRow, UserRow

SITE = "headhunter"
URL = f"/v1/users/u1/browser-sessions/{SITE}"


class FakeWorker:
    """Stands in for the browser worker: tracks a window and saves a session on confirm."""

    waiting = False
    unreachable = False
    factory: sessionmaker

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def login_start(self, **_kwargs: object) -> dict[str, object]:
        FakeWorker.waiting = True
        return {}

    async def login_is_waiting(self, **_kwargs: object) -> bool:
        if FakeWorker.unreachable:
            raise httpx.ConnectError("worker down")
        return FakeWorker.waiting

    async def login_confirm(self, *, user_id: str, site_key: str, **_kwargs: object) -> dict:
        FakeWorker.waiting = False
        with FakeWorker.factory() as db:
            db.add(
                BrowserSessionRow(
                    user_id=user_id,
                    site_key=site_key,
                    adapter_name=site_key,
                    encrypted_state_path="state-file",
                    status="available",
                )
            )
            db.commit()
        return {}

    async def login_cancel(self, **_kwargs: object) -> None:
        FakeWorker.waiting = False


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_BROWSER_STATE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_ARTIFACT_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("APP_ENVIRONMENT", "development")
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(UserRow(id="u1", display_name="U"))
        db.commit()

    FakeWorker.waiting = False
    FakeWorker.unreachable = False
    FakeWorker.factory = factory
    probe = {"live": True}
    monkeypatch.setattr("app.api.main.BrowserWorkerClient", FakeWorker)
    monkeypatch.setattr(
        "app.api.main.create_session_store",
        lambda _settings: SimpleNamespace(load=lambda _path: {"cookies": []}),
    )

    async def fake_probe(**_kwargs: object) -> BrowserSessionProbe:
        from datetime import UTC, datetime

        return BrowserSessionProbe(probe["live"], datetime.now(UTC))

    monkeypatch.setattr("app.api.main.probe_browser_session", fake_probe)

    def override() -> Iterator[Session]:
        with factory() as db:
            yield db

    app.dependency_overrides[session_scope] = override
    with TestClient(app) as test_client:
        test_client.probe = probe  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _status(client: TestClient, query: str = "") -> dict:
    response = client.get(f"/v1/users/u1/browser-sessions{query}")
    assert response.status_code == 200
    return next(item for item in response.json() if item["site_key"] == SITE)


def test_fresh_site_is_disconnected(client: TestClient) -> None:
    status = _status(client)

    assert status["state"] == "DISCONNECTED"
    assert status["is_authorized"] is False and status["is_waiting_for_login"] is False


def test_confirm_without_a_login_in_progress_is_a_clear_409(client: TestClient) -> None:
    response = client.post(f"{URL}/confirm")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "session_not_authenticating"
    assert detail["state"] == "DISCONNECTED" and detail["recovery"]


def test_full_login_lifecycle_reaches_ready_only_after_verification(client: TestClient) -> None:
    assert client.post(f"{URL}/start").status_code == 200
    assert _status(client)["state"] == "AUTHENTICATING"

    assert client.post(f"{URL}/confirm").status_code == 200
    status = _status(client)
    assert status["state"] == "READY"
    assert status["last_verified_at"] is not None and status["is_authorized"] is True


def test_inconclusive_probe_after_confirm_stays_authenticated(client: TestClient) -> None:
    client.probe["live"] = None  # type: ignore[attr-defined]
    client.post(f"{URL}/start")
    client.post(f"{URL}/confirm")

    assert _status(client)["state"] == "AUTHENTICATED"


def test_dead_session_becomes_reauth_required_with_a_reason(client: TestClient) -> None:
    client.post(f"{URL}/start")
    client.post(f"{URL}/confirm")
    client.probe["live"] = False  # type: ignore[attr-defined]

    status = _status(client, "?probe=true")

    assert status["state"] == "REAUTH_REQUIRED"
    assert status["is_authorized"] is False
    assert status["last_error"] and status["recovery_hint"]
    assert client.post(f"{URL}/confirm").status_code == 409  # no endless retries


def test_unreachable_worker_does_not_flip_a_pending_login(client: TestClient) -> None:
    client.post(f"{URL}/start")
    FakeWorker.unreachable = True

    assert _status(client)["state"] == "AUTHENTICATING"


def test_cancel_returns_to_login_required(client: TestClient) -> None:
    client.post(f"{URL}/start")

    assert client.post(f"{URL}/cancel").status_code == 200
    assert _status(client)["state"] == "LOGIN_REQUIRED"


def test_session_state_is_isolated_per_user(client: TestClient) -> None:
    other = client.post("/v1/users", json={"display_name": "Other"}).json()["id"]
    client.post(f"{URL}/start")
    client.post(f"{URL}/confirm")

    response = client.get(f"/v1/users/{other}/browser-sessions")
    other_status = next(item for item in response.json() if item["site_key"] == SITE)

    assert other_status["state"] == "DISCONNECTED"
    assert client.post(f"/v1/users/{other}/browser-sessions/{SITE}/confirm").status_code == 409
