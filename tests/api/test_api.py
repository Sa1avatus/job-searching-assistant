from fastapi.testclient import TestClient

from app.api.main import app, required_api_scope
from app.config import Settings, get_settings

client = TestClient(app)


def test_health_reports_review_mode() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "submission_mode": "review"}
    assert response.headers["x-correlation-id"]


def test_review_interface_has_security_boundary_and_no_embedded_remote_assets() -> None:
    response = client.get("/review")

    assert response.status_code == 200
    assert "Review queue" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "img-src 'self' blob:" in response.headers["content-security-policy"]
    assert "https://" not in response.text


def test_dashboard_wires_both_browser_search_sources_and_apply_routes() -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "discover-headhunter-vacancies" in response.text
    assert "discover-linkedin-vacancies" in response.text
    assert "apply-headhunter" in response.text
    assert "apply-linkedin" in response.text
    assert "source-linkedin" in response.text


def test_assessment_returns_grounded_gap() -> None:
    response = client.post(
        "/v1/assessments",
        json={
            "vacancy": {
                "source_url": "https://example.test/jobs/1",
                "title": "Engineer",
                "company": "Example",
                "required_skills": ["Python", "Kubernetes"],
            },
            "profile_facts": [
                {"category": "skill", "name": "Python", "value": "advanced", "is_verified": True}
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["missing_required_skills"] == ["kubernetes"]


def test_v1_route_requires_configured_api_key(monkeypatch) -> None:
    monkeypatch.setenv("APP_API_KEY", "configured-test-key")
    get_settings.cache_clear()
    try:
        unauthorized = client.post(
            "/v1/assessments",
            json={"vacancy": {}, "profile_facts": []},
        )
        authorized = client.post(
            "/v1/assessments",
            headers={"X-API-Key": "configured-test-key"},
            json={"vacancy": {}, "profile_facts": []},
        )

        assert unauthorized.status_code == 401
        assert authorized.status_code == 422
    finally:
        get_settings.cache_clear()


def test_scoped_api_key_enforces_least_privilege(monkeypatch) -> None:
    monkeypatch.delenv("APP_API_KEY", raising=False)
    monkeypatch.setenv(
        "APP_API_CLIENTS_JSON",
        '{"assessor":["assessments:write"],"profile-writer":["profiles:write"]}',
    )
    get_settings.cache_clear()
    try:
        forbidden = client.post(
            "/v1/users",
            headers={"X-API-Key": "assessor"},
            json={"display_name": "Candidate"},
        )
        allowed = client.post(
            "/v1/assessments",
            headers={"X-API-Key": "assessor"},
            json={
                "vacancy": {
                    "source_url": "https://example.test/jobs/scoped",
                    "title": "Engineer",
                    "company": "Example",
                },
                "profile_facts": [],
            },
        )

        assert forbidden.status_code == 403
        assert allowed.status_code == 200
    finally:
        get_settings.cache_clear()


def test_blank_api_key_does_not_enable_authentication_bypass() -> None:
    settings = Settings(api_key="")

    assert settings.api_clients() == {}
    assert required_api_scope("DELETE", "/v1/users/user-1") == "profiles:delete"
    assert required_api_scope("PATCH", "/v1/applications/application-1/materials") == "review:write"
    assert required_api_scope("POST", "/v1/applications/application-1/retry") == "review:write"
    assert required_api_scope("POST", "/v1/applications/application-1/resume") == "review:write"
    assert required_api_scope("GET", "/v1/evidence/artifact-1") == "review:read"


def test_connector_capabilities_expose_policy_boundaries() -> None:
    response = client.get("/v1/connectors")

    assert response.status_code == 200
    connectors = {item["name"]: item for item in response.json()}
    assert connectors["headhunter"]["vacancy_extraction"] == "browser_dom_automation"
    # Real submission is opt-in browser automation, off by default (APP_ENABLE_*_APPLY=false),
    # using a session the user captured by hand — see docs/known-limitations.md.
    assert connectors["linkedin-reference"]["submission_supported"] is True
    assert "no job-seeker API" in " ".join(connectors["linkedin-reference"]["known_limitations"])
    assert any(
        "LinkedIn's User Agreement prohibits this kind of automation" in limitation
        for limitation in connectors["linkedin-reference"]["known_limitations"]
    )


def test_browser_handoff_requires_manual_platform_actions() -> None:
    hh_response = client.post(
        "/v1/connectors/browser-handoff",
        json={"source_url": "https://spb.hh.ru/vacancy/123?tracking=test"},
    )
    linkedin_response = client.post(
        "/v1/connectors/browser-handoff",
        json={"source_url": "https://www.linkedin.com/jobs/view/engineer-456?tracking=test"},
    )

    assert hh_response.status_code == 200
    assert hh_response.json()["canonical_url"] == "https://hh.ru/vacancy/123"
    assert hh_response.json()["automated_actions_supported"] is False
    assert linkedin_response.status_code == 200
    assert linkedin_response.json()["canonical_url"] == "https://www.linkedin.com/jobs/view/456"
    assert linkedin_response.json()["mode"] == "manual_browser_handoff"
