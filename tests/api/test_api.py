import httpx
from fastapi.testclient import TestClient

from app.api.main import (
    app,
    build_model_providers,
    llm_is_configured,
    required_api_scope,
    serialize_discovery_outcomes,
)
from app.config import Settings, get_settings
from app.services.job_discovery import DiscoveryOutcome

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
    assert "discover-greenhouse-vacancies" in response.text
    assert "apply-headhunter" in response.text
    assert "apply-linkedin" in response.text
    assert "source-linkedin" in response.text
    assert "/v1/llm/models" in response.text
    assert "llm-preference" in response.text
    assert "browser-sessions" in response.text
    assert "headhunter-login" in response.text
    assert "linkedin-login" in response.text
    assert "Я вошёл — сохранить" in response.text
    assert ".doc,.txt,.rtf,.odt,.html,.htm,.md" in response.text
    assert "Все сохранённые вакансии" in response.text
    assert "/vacancies?" in response.text
    assert 'data-menu="sessions"' in response.text
    assert "vacancy-previous" in response.text
    assert "source-greenhouse" in response.text
    assert "company-blacklist" in response.text
    assert "reject-vacancy" in response.text
    assert "match-meter" in response.text
    assert 'id="resume-selector"' in response.text
    assert 'id="search-resume-selector"' in response.text
    assert "/active-cv-file" in response.text
    assert "cv_file_id: cvFileId" in response.text
    assert "dashboardSearchResults:" in response.text
    assert "persistSearchResults(outcomes)" in response.text
    assert "restoreSearchResults()" in response.text
    assert "job-assistant-login-" in response.text
    assert ":7900/vnc.html" in response.text
    assert "autoconnect" in response.text


def test_discovery_outcome_with_slots_is_serialized_for_dashboard() -> None:
    outcome = DiscoveryOutcome(
        application_id="application-1",
        vacancy_id="vacancy-1",
        title="AI Engineer",
        company="Example",
        source_url="https://hh.ru/vacancy/123",
        match_score=80,
        status="created",
        application_status="awaiting_review",
        vacancy_summary="Build AI systems.",
        work_format="hybrid",
    )

    response = serialize_discovery_outcomes([outcome])

    assert response[0].application_id == "application-1"
    assert response[0].match_score == 80
    assert response[0].application_status == "awaiting_review"
    assert response[0].vacancy_summary == "Build AI systems."
    assert response[0].work_format == "hybrid"
    assert response[0].salary_text == ""
    assert response[0].employment_types == []


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


def test_blank_anthropic_key_falls_through_to_configured_gemini() -> None:
    settings = Settings(anthropic_api_key="", gemini_api_key="gemini-test-key")

    assert llm_is_configured(settings) is True
    providers = build_model_providers(httpx.AsyncClient(), settings)
    assert [provider.name for provider in providers] == ["gemini"]


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


def test_dashboard_exposes_stateful_metadata_and_non_disruptive_rejection() -> None:
    response = client.get("/dashboard")
    html = response.text

    assert response.status_code == 200
    # Work-format badge with Russian labels for all format values
    assert "workFormatLabels" in html
    assert "Удалённо" in html
    assert "Гибрид" in html
    assert "Офис" in html
    # Accessible summary tooltip
    assert "createSummaryAffordance" in html
    assert "summary-affordance" in html
    # Cache version bumped for new metadata fields
    assert "SEARCH_RESULTS_VERSION = 4" in html
    # New fields normalized and persisted in search cache
    assert "application_status" in html
    assert "vacancy_summary" in html
    assert "work_format" in html
    assert "salary_text" in html
    assert "employment_types" in html
    assert "key_skills" in html
    assert "createKeySkillTags" in html
    assert "createStatusEditor" in html
    assert "setApplicationStatus" in html
    assert "Я откликнулся вручную" in html
    assert 'id="vacancy-work-format"' in html
    assert 'id="vacancy-employment-type"' in html
    assert 'id="manual-skill"' in html
    assert 'id="language-toggle"' in html
    assert "dashboardLanguage" in html
    assert 'id="language-menu"' in html
    assert 'aria-haspopup="listbox"' in html
    assert '<svg class="language-flag"' in html
    assert "LANGUAGE_OPTIONS" in html
    assert "createLanguageFlag" in html
    assert "selectedLanguage.names[selectedLanguage.code]" in html
    assert "selectLanguage(language.code)" in html
    assert "event.key === 'Escape'" in html
    assert "currentLanguage = currentLanguage === 'ru' ? 'en' : 'ru'" not in html
    assert "🇬🇧" not in html
    assert "🇷🇺" not in html
    # Submitted / interview vacancies show disabled primary action
    assert "Отклик отправлен" in html
    assert "isSubmissionConfirmed" in html
    assert 'id="delete-resume"' in html
    assert "generate-materials" in html
    assert "cover_letter_language_matches" in html
    assert "application_status !== item.application_status" in html
    assert "recalculate-match" in html
    assert "Рассчитать подробно" in html
    # Missing skills rendered as chip tags
    assert "appendMissingSkillsTags" in html
    # Cache status update after confirmed submission
    assert "updateCachedSearchResultStatus" in html


def test_review_page_exposes_selected_application_navigation_and_status_editor() -> None:
    response = client.get("/review?application_id=application-123")
    html = response.text

    assert response.status_code == 200
    assert 'class="sidebar"' in html
    assert "selectedApplicationId" in html
    assert "application_id" in html
    assert "createStatusEditor" in html
    assert "createKeySkillTags" in html
    assert "Материалы и решения" in html


def test_review_has_metadata_tags_source_button_and_in_place_rejection() -> None:
    response = client.get("/review")
    html = response.text

    assert response.status_code == 200
    # Work-format badge with Russian labels
    assert "workFormatLabels" in html
    assert "Удалённо" in html
    # Accessible summary tooltip
    assert "summary-affordance" in html
    # Missing skills from dedicated field and derived from warnings
    assert "missing_required_skills" in html
    assert "Missing required skill:" in html
    assert "employment_types" in html
    assert "salary_text" in html
    assert 'id="language-toggle"' in html
    assert "dashboardLanguage" in html
    assert 'id="language-menu"' in html
    assert 'aria-haspopup="listbox"' in html
    assert '<svg class="language-flag"' in html
    assert "LANGUAGE_OPTIONS" in html
    assert "createLanguageFlag" in html
    assert "selectedLanguage.names[selectedLanguage.code]" in html
    assert "selectLanguage(language.code)" in html
    assert "event.key === 'Escape'" in html
    assert "currentLanguage = currentLanguage === 'ru' ? 'en' : 'ru'" not in html
    assert "🇬🇧" not in html
    assert "🇷🇺" not in html
    # Source vacancy moved to bottom actions as button
    assert "link-btn" in html
    assert "Открыть вакансию" in html
    # Reject removes only affected card without queue reload
    assert "decision === 'reject'" in html
    assert "closest('article')" in html
