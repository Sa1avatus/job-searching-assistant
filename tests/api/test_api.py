import json
from types import SimpleNamespace

import httpx
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from ui_http import get_ui_page

from app.api.main import (
    app,
    build_model_providers,
    greenhouse_http_client,
    llm_http_client,
    llm_is_configured,
    required_api_scope,
    serialize_discovery_outcomes,
)
from app.config import Settings, get_settings
from app.services.job_discovery import DiscoveryOutcome, JobDiscoveryService
from app.storage.database import Base, session_scope
from app.storage.tables import ApplicationMatchResultRow, ApplicationRow, UserRow, VacancyRow

client = TestClient(app)


def test_health_reports_review_mode() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "submission_mode": "review"}
    assert response.headers["x-correlation-id"]


def test_review_interface_has_security_boundary_and_no_embedded_remote_assets() -> None:
    response = get_ui_page(client, "/review")

    assert response.status_code == 200
    assert "Review queue" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "img-src 'self' blob:" in response.headers["content-security-policy"]
    assert "https://" not in response.text


def test_dashboard_wires_both_browser_search_sources_and_apply_routes() -> None:
    response = get_ui_page(client, "/dashboard")

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
    assert "site-definition-key" in response.text
    assert "site-definition-login-url" in response.text
    assert "add-site-definition" in response.text
    assert "ensureBrowserSessionCard" in response.text
    assert "encodeURIComponent(site)" in response.text
    assert 'id="site-field-mapping-panel"' in response.text
    assert 'id="site-field-site"' in response.text
    assert 'id="site-field-mappings"' in response.text
    assert "loadSiteFieldMappings" in response.text
    assert "/site-fields/${field.id}/mapping" in response.text
    assert "/effective-value" in response.text
    assert "/overrides" in response.text
    assert "Использовать общее значение" in response.text
    assert "Переопределить только это поле" in response.text
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
    assert "SEARCH_RESULTS_VERSION = 6" in response.text
    assert "candidate.application_id !== item.application_id" in response.text
    assert "insertProgressiveSearchResult" in response.text
    assert "discover-vacancies-stream" in response.text
    assert "direct_rerank: Boolean" in response.text
    assert "document.querySelector('#rerank-all').click()" not in response.text
    assert "response.body.getReader()" in response.text
    assert "event.event === 'vacancy'" in response.text
    assert "event.event === 'materials_ready'" in response.text
    assert "event.event === 'matching_ready'" in response.text
    assert "coverText.readOnly = item.materials_status === 'processing'" in response.text
    assert "completedSources" in response.text
    assert "GREENHOUSE_BOARDS_STORAGE_KEY" in response.text
    assert "greenhouseBoardsInput.addEventListener('input', saveGreenhouseBoards)" in response.text
    assert "greenhouseBoardsInput.value = localStorage.getItem" in response.text
    assert "fetchWithTimeout" in response.text
    assert "Saving the manual application status" in response.text
    assert "Manual application marked as submitted" in response.text
    assert "fetchWithTimeout(" in response.text
    assert "150000" in response.text
    assert "card-actions" in response.text
    assert "more-actions__menu" in response.text
    assert "status-popup" in response.text
    assert "Перегенерировать письмо" in response.text
    assert "coverHeader.append(regenerateLetter)" in response.text
    assert "Sources completed" in response.text
    assert "Promise.allSettled" not in response.text
    assert "Promise.all(sources.map" not in response.text
    assert "job-assistant-login-" in response.text
    assert ":7900/vnc.html" in response.text
    assert "autoconnect" in response.text


def test_openapi_reports_current_version() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["version"] == "2.0.0"


def test_discovery_outcome_with_slots_is_serialized_for_dashboard() -> None:
    outcome = DiscoveryOutcome(
        application_id="application-1",
        vacancy_id="vacancy-1",
        title="AI Engineer",
        company="Example",
        source_url="https://hh.ru/vacancy/123",
        location="Москва, Россия",
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
    assert response[0].location == "Москва, Россия"
    assert response[0].vacancy_summary == "Build AI systems."
    assert response[0].work_format == "hybrid"
    assert response[0].salary_text == ""
    assert response[0].employment_types == []


def test_discovery_stream_emits_vacancy_before_completion(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.api.main.SessionFactory", session_factory)
    monkeypatch.setattr(
        "app.api.main.get_settings",
        lambda: Settings(_env_file=None, matching_v2_enabled=False),
    )
    user_id = "stream-test-user"
    with session_factory() as session:
        session.add(UserRow(id=user_id, display_name="Stream Test Candidate"))
        session.commit()
    outcome = DiscoveryOutcome(
        application_id="application-stream-1",
        vacancy_id="vacancy-stream-1",
        title="Streaming Engineer",
        company="Example",
        source_url="https://boards.greenhouse.io/example/jobs/1",
        location="Berlin, Germany",
        match_score=91,
        status="created",
        application_status="awaiting_review",
        vacancy_summary="Build streaming APIs.",
        work_format="remote",
    )

    observed_limits: list[int] = []

    async def discover_greenhouse(*_args, on_outcome=None, **kwargs):
        assert on_outcome is not None
        observed_limits.append(kwargs["limit"])
        await on_outcome(outcome)
        return [outcome]

    monkeypatch.setattr(JobDiscoveryService, "discover_greenhouse_vacancies", discover_greenhouse)

    with client.stream(
        "POST",
        f"/v1/users/{user_id}/discover-vacancies-stream",
        json={
            "sources": ["greenhouse"],
            "board_urls": ["https://boards.greenhouse.io/example"],
            "search_text": "streaming",
        },
    ) as response:
        events = [json.loads(line) for line in response.iter_lines() if line]

    assert response.status_code == 200
    event_names = [event["event"] for event in events]
    assert event_names[0] == "vacancy"
    assert event_names[-1] == "complete"
    assert "enrichment_started" in event_names
    assert "matching_ready" in event_names
    assert {"materials_ready", "materials_error"} & set(event_names)
    assert event_names.index("vacancy") < event_names.index("source_complete")
    assert events[0]["item"]["title"] == "Streaming Engineer"
    assert observed_limits == [15]


def test_direct_rerank_stream_enriches_before_emitting_sorted_vacancies(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.api.main.SessionFactory", session_factory)
    monkeypatch.setattr(
        "app.api.main.get_settings",
        lambda: Settings(
            _env_file=None,
            matching_v2_enabled=True,
            rag_enabled=True,
            rag_service_url="http://rag.test",
            rag_api_key="rag-key",
            rag_project_id="project-1",
        ),
    )
    user_id = "direct-rerank-user"
    with session_factory() as session:
        session.add(UserRow(id=user_id, display_name="Direct Candidate"))
        for suffix, score in (("low", 40), ("high", 90)):
            session.add(
                VacancyRow(
                    id=f"vacancy-{suffix}",
                    source_url=f"https://boards.greenhouse.io/example/jobs/{suffix}",
                    title=f"{suffix.title()} score",
                    company="Example",
                )
            )
            session.add(
                ApplicationRow(
                    id=f"direct-{suffix}",
                    user_id=user_id,
                    vacancy_id=f"vacancy-{suffix}",
                    status="awaiting_review",
                    match_score=score,
                )
            )
        session.commit()
    outcomes = [
        DiscoveryOutcome(
            application_id="direct-low",
            vacancy_id="vacancy-low",
            title="Lower score",
            company="Example",
            source_url="https://boards.greenhouse.io/example/jobs/low",
            location="Remote",
            match_score=40,
            status="created",
            application_status="awaiting_review",
            vacancy_summary="Lower.",
            work_format="remote",
        ),
        DiscoveryOutcome(
            application_id="direct-high",
            vacancy_id="vacancy-high",
            title="Higher score",
            company="Example",
            source_url="https://boards.greenhouse.io/example/jobs/high",
            location="Remote",
            match_score=90,
            status="created",
            application_status="awaiting_review",
            vacancy_summary="Higher.",
            work_format="remote",
        ),
    ]
    operation_order: list[str] = []

    async def ingest_profile(_service, _user_id):
        operation_order.append("profile")
        return SimpleNamespace(document_id="profile-doc", status="indexed")

    async def ingest_vacancy(_service, _vacancy_id, **_kwargs):
        operation_order.append("vacancy")
        return SimpleNamespace(document_id="vacancy-doc", status="indexed")

    def schedule_matching(service, application_id, *, force=False, priority=100):
        # direct_rerank now uses smart recalculation (unchanged content reuses the cache)
        # instead of always discarding it - see discover_vacancies_stream.
        assert force is False
        assert priority == 50
        operation_order.append("matching")
        application = service._session.get(ApplicationRow, application_id)
        assert application is not None
        service._session.merge(
            ApplicationMatchResultRow(
                application_id=application_id,
                status="scored",
                final_score=application.match_score,
            )
        )
        service._session.commit()
        return SimpleNamespace(id=f"task-{application_id}")

    monkeypatch.setattr(
        "app.matching.rag_client.create_rag_client",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "app.services.profile_rag_ingestion.ProfileRagIngestionService.ingest_profile",
        ingest_profile,
    )
    monkeypatch.setattr(
        "app.services.vacancy_rag_ingestion.VacancyRagIngestionService.ingest_vacancy",
        ingest_vacancy,
    )
    monkeypatch.setattr("app.api.main.MatchingJobService.schedule", schedule_matching)

    direct_limits: list[int] = []

    async def discover_greenhouse(*_args, on_outcome=None, **kwargs):
        assert on_outcome is not None
        direct_limits.append(kwargs["limit"])
        for outcome in outcomes:
            await on_outcome(outcome)
        return outcomes

    monkeypatch.setattr(JobDiscoveryService, "discover_greenhouse_vacancies", discover_greenhouse)

    with client.stream(
        "POST",
        f"/v1/users/{user_id}/discover-vacancies-stream",
        json={
            "sources": ["greenhouse"],
            "board_urls": ["https://boards.greenhouse.io/example"],
            "direct_rerank": True,
        },
    ) as response:
        events = [json.loads(line) for line in response.iter_lines() if line]

    vacancy_events = [event for event in events if event["event"] == "vacancy"]
    assert [event["item"]["title"] for event in vacancy_events] == [
        "Higher score",
        "Lower score",
    ]
    assert all(event["item"]["matching_status"] == "scored" for event in vacancy_events)
    assert "enrichment_started" not in [event["event"] for event in events]
    assert direct_limits == [45]
    # The profile/CV is identical for every vacancy in one search, so it is only
    # ingested into RAG once per search, not once per vacancy.
    assert operation_order == [
        "profile",
        "vacancy",
        "matching",
        "vacancy",
        "matching",
    ]


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
    response = get_ui_page(client, "/dashboard")
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
    assert "SEARCH_RESULTS_VERSION = 6" in html
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
    assert 'id="send-resume-to-rag"' in html
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
    response = get_ui_page(client, "/review?application_id=application-123")
    html = response.text

    assert response.status_code == 200
    assert 'class="sidebar"' in html
    assert "selectedApplicationId" in html
    assert "application_id" in html
    assert "createStatusEditor" in html
    assert "createKeySkillTags" in html
    assert "Материалы и решения" in html


def test_review_has_metadata_tags_source_button_and_in_place_rejection() -> None:
    response = get_ui_page(client, "/review")
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


def _discovery_test_env(monkeypatch):
    """Create an in-memory SQLite database with a user, vacancy, and an
    awaiting-review application, and return the session factory and ids."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    user_id = "discovery-test-user"
    vacancy_id = "vacancy-discovery-1"
    application_id = "application-discovery-1"
    with session_factory() as session:
        session.add(UserRow(id=user_id, display_name="Discovery Test Candidate"))
        session.add(
            VacancyRow(
                id=vacancy_id,
                source_url="https://example.test/vacancy/1",
                title="Discovery Engineer",
                company="Example",
            )
        )
        session.add(
            ApplicationRow(
                id=application_id,
                user_id=user_id,
                vacancy_id=vacancy_id,
                status="awaiting_review",
                match_score=80,
            )
        )
        session.commit()
    return session_factory, user_id, vacancy_id, application_id


def _make_outcome(application_id: str, vacancy_id: str, url: str) -> DiscoveryOutcome:
    return DiscoveryOutcome(
        application_id=application_id,
        vacancy_id=vacancy_id,
        title="Discovery Engineer",
        company="Example",
        source_url=url,
        location="Remote",
        match_score=80,
        status="created",
        application_status="awaiting_review",
        vacancy_summary="Build discovery systems.",
        work_format="remote",
    )


class _StubProvider:
    """Non-empty provider stand-in so the discover endpoints enter the drafting loop."""

    def __init__(self) -> None:
        self.name = "stub-provider"


def _setup_discovery_test(monkeypatch, settings: Settings):
    """Override dependencies and return (test_client, sentinel_llm_client, captured, ids)."""
    session_factory, user_id, vacancy_id, application_id = _discovery_test_env(monkeypatch)
    monkeypatch.setattr("app.api.main.get_settings", lambda: settings)

    sentinel_llm_client = object.__new__(httpx.AsyncClient)
    captured: list[httpx.AsyncClient] = []

    def _capture_provider(http_client, session, user_id, settings, purpose=None):
        captured.append(http_client)
        return (_StubProvider(),)

    monkeypatch.setattr("app.api.main.build_user_model_providers", _capture_provider)

    def _always_needs_refresh(self, application_id: str) -> bool:
        return True

    monkeypatch.setattr(
        "app.services.materials_generation.MaterialsGenerationService.needs_material_refresh",
        _always_needs_refresh,
    )

    overrides = app.dependency_overrides
    overrides[session_scope] = lambda: session_factory()
    overrides[llm_http_client] = lambda: sentinel_llm_client

    test_client = TestClient(app)
    return test_client, sentinel_llm_client, captured, user_id, vacancy_id, application_id


def _cleanup_discovery_test():
    app.dependency_overrides.clear()


def test_discover_headhunter_vacancies_routes_llm_client_and_handles_draft_failure(
    monkeypatch,
) -> None:
    settings = Settings(_env_file=None, matching_v2_enabled=False)
    test_client, sentinel_llm, captured, user_id, _, application_id = _setup_discovery_test(
        monkeypatch, settings
    )
    try:
        outcome = _make_outcome(application_id, "vacancy-discovery-1", "https://hh.ru/vacancy/1")

        async def fake_discover_headhunter(self, user_id, headhunter_adapter=None, **kwargs):
            return [outcome]

        monkeypatch.setattr(
            JobDiscoveryService, "discover_headhunter_vacancies", fake_discover_headhunter
        )

        draft_calls: list[str] = []

        async def failing_draft(self, application_id, **kwargs):
            draft_calls.append(application_id)
            raise RuntimeError("draft failed")

        monkeypatch.setattr(
            "app.services.materials_generation.MaterialsGenerationService.draft_materials",
            failing_draft,
        )

        with structlog.testing.capture_logs() as logs:
            response = test_client.post(
                f"/v1/users/{user_id}/discover-headhunter-vacancies",
                json={"search_text": "engineer", "limit": 15},
            )

        assert response.status_code == 200
        assert len(captured) == 1
        assert captured[0] is sentinel_llm
        assert draft_calls == [application_id]
        warnings = [
            entry
            for entry in logs
            if entry.get("event") == "draft_materials_failed"
            and entry.get("application_id") == application_id
            and entry.get("error_type") == "RuntimeError"
        ]
        assert len(warnings) == 1
        data = response.json()
        assert data[0]["application_id"] == application_id
    finally:
        _cleanup_discovery_test()


def test_discover_linkedin_vacancies_routes_llm_client_and_handles_draft_failure(
    monkeypatch,
) -> None:
    settings = Settings(
        _env_file=None,
        matching_v2_enabled=False,
        enable_linkedin_apply=True,
    )
    test_client, sentinel_llm, captured, user_id, _, application_id = _setup_discovery_test(
        monkeypatch, settings
    )
    try:
        outcome = _make_outcome(
            application_id, "vacancy-discovery-1", "https://www.linkedin.com/jobs/view/1"
        )

        async def fake_discover_linkedin(self, user_id, linkedin_adapter=None, **kwargs):
            return [outcome]

        monkeypatch.setattr(
            JobDiscoveryService, "discover_linkedin_vacancies", fake_discover_linkedin
        )

        draft_calls: list[str] = []

        async def failing_draft(self, application_id, **kwargs):
            draft_calls.append(application_id)
            raise RuntimeError("draft failed")

        monkeypatch.setattr(
            "app.services.materials_generation.MaterialsGenerationService.draft_materials",
            failing_draft,
        )

        with structlog.testing.capture_logs() as logs:
            response = test_client.post(
                f"/v1/users/{user_id}/discover-linkedin-vacancies",
                json={"search_text": "engineer", "limit": 15},
            )

        assert response.status_code == 200
        assert len(captured) == 1
        assert captured[0] is sentinel_llm
        assert draft_calls == [application_id]
        warnings = [
            entry
            for entry in logs
            if entry.get("event") == "draft_materials_failed"
            and entry.get("application_id") == application_id
            and entry.get("error_type") == "RuntimeError"
        ]
        assert len(warnings) == 1
        data = response.json()
        assert data[0]["application_id"] == application_id
    finally:
        _cleanup_discovery_test()


def test_discover_greenhouse_vacancies_routes_clients_and_handles_draft_failure(
    monkeypatch,
) -> None:
    settings = Settings(_env_file=None, matching_v2_enabled=False)
    session_factory, user_id, vacancy_id, application_id = _discovery_test_env(monkeypatch)
    monkeypatch.setattr("app.api.main.get_settings", lambda: settings)

    sentinel_llm_client = object.__new__(httpx.AsyncClient)
    sentinel_greenhouse_client = object.__new__(httpx.AsyncClient)
    captured_llm: list[httpx.AsyncClient] = []
    captured_greenhouse: list[httpx.AsyncClient] = []

    def _capture_provider(http_client, session, user_id, settings, purpose=None):
        captured_llm.append(http_client)
        return (_StubProvider(),)

    monkeypatch.setattr("app.api.main.build_user_model_providers", _capture_provider)

    def _always_needs_refresh(self, application_id: str) -> bool:
        return True

    monkeypatch.setattr(
        "app.services.materials_generation.MaterialsGenerationService.needs_material_refresh",
        _always_needs_refresh,
    )

    class _FakeGreenhouseApi:
        def __init__(self, http_client: httpx.AsyncClient) -> None:
            captured_greenhouse.append(http_client)
            self._http_client = http_client

    monkeypatch.setattr("app.api.main.GreenhouseJobBoardApi", _FakeGreenhouseApi)

    overrides = app.dependency_overrides
    overrides[session_scope] = lambda: session_factory()
    overrides[llm_http_client] = lambda: sentinel_llm_client
    overrides[greenhouse_http_client] = lambda: sentinel_greenhouse_client

    test_client = TestClient(app)
    try:
        outcome = _make_outcome(
            application_id, vacancy_id, "https://boards.greenhouse.io/example/jobs/1"
        )

        async def fake_discover_greenhouse(self, user_id, greenhouse_adapter=None, **kwargs):
            return [outcome]

        monkeypatch.setattr(
            JobDiscoveryService, "discover_greenhouse_vacancies", fake_discover_greenhouse
        )

        draft_calls: list[str] = []

        async def failing_draft(self, application_id, **kwargs):
            draft_calls.append(application_id)
            raise RuntimeError("draft failed")

        monkeypatch.setattr(
            "app.services.materials_generation.MaterialsGenerationService.draft_materials",
            failing_draft,
        )

        with structlog.testing.capture_logs() as logs:
            response = test_client.post(
                f"/v1/users/{user_id}/discover-greenhouse-vacancies",
                json={
                    "board_urls": ["https://boards.greenhouse.io/example"],
                    "search_text": "engineer",
                    "limit": 15,
                },
            )

        assert response.status_code == 200
        assert len(captured_llm) == 1
        assert captured_llm[0] is sentinel_llm_client
        assert len(captured_greenhouse) == 1
        assert captured_greenhouse[0] is sentinel_greenhouse_client
        assert draft_calls == [application_id]
        warnings = [
            entry
            for entry in logs
            if entry.get("event") == "draft_materials_failed"
            and entry.get("application_id") == application_id
            and entry.get("error_type") == "RuntimeError"
        ]
        assert len(warnings) == 1
        data = response.json()
        assert data[0]["application_id"] == application_id
    finally:
        _cleanup_discovery_test()
