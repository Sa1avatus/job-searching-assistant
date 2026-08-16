import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from adapters.job_boards.browser_apply_common import CaptchaChallenge
from adapters.job_boards.greenhouse_api import ExtractedGreenhouseJob, GreenhouseSearchHit
from adapters.job_boards.headhunter_browser import ExtractedHeadHunterVacancy, HeadHunterSearchHit
from adapters.job_boards.linkedin_browser import ExtractedLinkedInVacancy, LinkedInSearchHit
from app.domain.forms import FormField, FormFieldType
from app.services.job_discovery import (
    JobDiscoveryService,
    NoSearchKeywordsError,
    _contains_skill,
    _detect_extracted_work_format,
    _normalize_skill,
    build_search_queries,
)
from app.services.recruitment import RecruitmentService
from app.storage.database import Base
from app.storage.tables import ApplicationRow, CvFileRow, VacancyRow


class _FakeAdapter:
    def __init__(self, hits: list[HeadHunterSearchHit], *, raise_captcha: bool = False) -> None:
        self._hits = hits
        self._raise_captcha = raise_captcha
        self.search_calls: list[tuple[str, list[str]]] = []

    async def search(
        self, *, text: str, location_names: list[str], limit: int
    ) -> list[HeadHunterSearchHit]:
        self.search_calls.append((text, location_names))
        if self._raise_captcha:
            raise CaptchaChallenge("blocked")
        return self._hits[:limit]

    async def extract_vacancy(self, url: str) -> ExtractedHeadHunterVacancy:
        return ExtractedHeadHunterVacancy(
            source_url=url,
            title="Backend Engineer",
            company="Example Co",
            location="Москва",
            description_text="Build things with Python.",
            required_skills=("Python",),
            form_fields=(
                FormField("resume", "Резюме", FormFieldType.FILE, True, semantic_category="resume"),
            ),
            requires_sensitive_review=False,
            salary_text="от 250 000 ₽",
            employment_text="Удалённо, полная занятость",
        )


class _FakeLinkedInAdapter:
    def __init__(self, *, application_submitted: bool = False) -> None:
        self.application_submitted = application_submitted

    async def search(
        self, *, text: str, location_names: list[str], limit: int
    ) -> list[LinkedInSearchHit]:
        return [
            LinkedInSearchHit(
                job_id="2",
                source_url="https://www.linkedin.com/jobs/view/2",
                title="Backend Engineer",
                company="Example Co",
            )
        ][:limit]

    async def extract_vacancy(self, url: str) -> ExtractedLinkedInVacancy:
        return ExtractedLinkedInVacancy(
            source_url=url,
            title="Backend Engineer",
            company="Example Co",
            location="Remote",
            description_text="Python services",
            has_easy_apply=False,
            salary_text="$120,000 - $150,000",
            employment_text="Remote, Full-time",
            application_submitted=self.application_submitted,
        )


class _MultiLinkedInAdapter(_FakeLinkedInAdapter):
    def __init__(self, hits: list[LinkedInSearchHit]) -> None:
        super().__init__()
        self._hits = hits

    async def search(
        self, *, text: str, location_names: list[str], limit: int
    ) -> list[LinkedInSearchHit]:
        return self._hits[:limit]

    async def extract_vacancy(self, url: str) -> ExtractedLinkedInVacancy:
        extracted = await super().extract_vacancy(url)
        return ExtractedLinkedInVacancy(
            source_url=url,
            title=extracted.title,
            company=extracted.company,
            location=extracted.location,
            description_text=extracted.description_text,
            has_easy_apply=extracted.has_easy_apply,
            salary_text=extracted.salary_text,
            employment_text=extracted.employment_text,
        )


class _EmptyMetadataLinkedInAdapter(_FakeLinkedInAdapter):
    async def extract_vacancy(self, url: str) -> ExtractedLinkedInVacancy:
        return ExtractedLinkedInVacancy(
            source_url=url,
            title="Backend Engineer",
            company="Example Co",
            location="",
            description_text="Build reliable software.",
            has_easy_apply=False,
        )


class _FakeGreenhouseAdapter:
    async def list_jobs(self, board_url: str) -> tuple[GreenhouseSearchHit, ...]:
        return (
            GreenhouseSearchHit(
                source_url="https://boards.greenhouse.io/example/jobs/3",
                title="Python Engineer",
                company="Green Example",
                location="Remote",
                description_text="Build Python services",
            ),
        )

    async def extract_job(self, url: str) -> ExtractedGreenhouseJob:
        return ExtractedGreenhouseJob(
            source_url=url,
            job_id=3,
            title="Python Engineer",
            company="Green Example",
            location="Remote",
            description_text="Build Python services",
            language="en",
            application_deadline=None,
            form_fields=(),
            requires_sensitive_review=False,
            evidence_api_url="https://boards-api.greenhouse.io/v1/boards/example/jobs/3",
            salary_text="$140,000",
            employment_text="Hybrid contract role",
        )


class _MultiGreenhouseAdapter(_FakeGreenhouseAdapter):
    def __init__(self, hits: tuple[GreenhouseSearchHit, ...]) -> None:
        self._hits = hits

    async def list_jobs(self, board_url: str) -> tuple[GreenhouseSearchHit, ...]:
        return self._hits

    async def extract_job(self, url: str) -> ExtractedGreenhouseJob:
        extracted = await super().extract_job(url)
        return ExtractedGreenhouseJob(
            source_url=url,
            job_id=int(url.rsplit("/", 1)[-1]),
            title=extracted.title,
            company=extracted.company,
            location=extracted.location,
            description_text=extracted.description_text,
            language=extracted.language,
            application_deadline=extracted.application_deadline,
            form_fields=extracted.form_fields,
            requires_sensitive_review=extracted.requires_sensitive_review,
            evidence_api_url=extracted.evidence_api_url,
            salary_text=extracted.salary_text,
            employment_text=extracted.employment_text,
        )


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _create_rejected_application(
    session, *, user_id: str, source_url: str, adapter_name: str
) -> None:
    service = RecruitmentService(session)
    vacancy = service.create_vacancy(
        source_url=source_url,
        title="Old Python Engineer",
        company="Old Example",
        required_skills=["Python"],
        preferred_skills=[],
        description_text="Python services",
        adapter_name=adapter_name,
    )
    application = service.prepare_application(user_id, vacancy.id)
    application.status = "rejected"
    session.commit()


def test_discover_creates_vacancy_and_application() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            user = RecruitmentService(session).create_user("Candidate")
            RecruitmentService(session).add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            user_id = user.id

        adapter = _FakeAdapter(
            [
                HeadHunterSearchHit(
                    vacancy_id="1", source_url="https://hh.ru/vacancy/1", title="x", company="y"
                )
            ]
        )
        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_headhunter_vacancies(
                user_id, headhunter_adapter=adapter, locations=[]
            )
        assert len(outcomes) == 1
        assert outcomes[0].status == "created"
        assert outcomes[0].title == "Backend Engineer"
        assert outcomes[0].application_status == "awaiting_review"
        assert outcomes[0].vacancy_summary == "Build things with Python."
        assert outcomes[0].work_format == "remote"
        assert outcomes[0].salary_text == "от 250 000 ₽"
        assert outcomes[0].employment_types == ("full_time",)
        assert adapter.search_calls == [("Python", [])]

    asyncio.run(run())


def test_discover_uses_selected_resume_keywords_skills_and_file() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            recruitment = RecruitmentService(session)
            user = recruitment.create_user("Multiple resumes")
            cv_file = CvFileRow(
                user_id=user.id,
                original_filename="python.txt",
                storage_path="python.txt",
                content_type="text/plain",
                sha256="a" * 64,
                size_bytes=10,
            )
            session.add(cv_file)
            session.commit()
            recruitment.save_cv_profile(
                user.id,
                cv_file.id,
                skills=["Python", "FastAPI"],
                experience_summary="Python developer",
                search_keywords="Python FastAPI",
                years_of_experience=5,
            )
            adapter = _FakeAdapter(
                [
                    HeadHunterSearchHit(
                        "1", "https://hh.ru/vacancy/1", "Backend Engineer", "Example Co"
                    )
                ]
            )

            outcomes = await JobDiscoveryService(session).discover_headhunter_vacancies(
                user.id,
                headhunter_adapter=adapter,
                locations=[],
                limit=1,
                cv_file_id=cv_file.id,
            )
            application = session.get(ApplicationRow, outcomes[0].application_id)

            assert adapter.search_calls[0][0] == "Python FastAPI"
            assert application is not None
            assert application.selected_cv_file_id == cv_file.id
            assert application.match_score > 0
            assert outcomes[0].application_status == "awaiting_review"
            assert outcomes[0].vacancy_summary == "Build things with Python."

    asyncio.run(run())


def test_discover_marks_existing_application_as_already_existed() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            user = RecruitmentService(session).create_user("Candidate")
            RecruitmentService(session).add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            user_id = user.id

        hit = HeadHunterSearchHit(
            vacancy_id="1", source_url="https://hh.ru/vacancy/1", title="x", company="y"
        )
        adapter = _FakeAdapter([hit])
        with session_factory() as session:
            first = await JobDiscoveryService(session).discover_headhunter_vacancies(
                user_id, headhunter_adapter=adapter, locations=[]
            )
        with session_factory() as session:
            second = await JobDiscoveryService(session).discover_headhunter_vacancies(
                user_id, headhunter_adapter=adapter, locations=[]
            )
        assert first[0].status == "created"
        assert first[0].application_status == "awaiting_review"
        assert second[0].status == "already_existed"
        assert second[0].application_status == "awaiting_review"
        assert second[0].application_id == first[0].application_id
        assert second[0].vacancy_summary == first[0].vacancy_summary
        assert second[0].work_format == first[0].work_format

    asyncio.run(run())


def test_discover_requires_verified_skills_or_explicit_search_text() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            user = RecruitmentService(session).create_user("Candidate")
            user_id = user.id
        adapter = _FakeAdapter([])
        with session_factory() as session:
            try:
                await JobDiscoveryService(session).discover_headhunter_vacancies(
                    user_id, headhunter_adapter=adapter, locations=[]
                )
            except NoSearchKeywordsError:
                return
        raise AssertionError("expected NoSearchKeywordsError")

    asyncio.run(run())


def test_discover_returns_empty_list_on_captcha_instead_of_raising() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            user = RecruitmentService(session).create_user("Candidate")
            RecruitmentService(session).add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            user_id = user.id
        adapter = _FakeAdapter([], raise_captcha=True)
        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_headhunter_vacancies(
                user_id, headhunter_adapter=adapter, locations=[]
            )
        assert outcomes == []

    asyncio.run(run())


def test_discover_passes_explicit_search_text_over_facts() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            user = RecruitmentService(session).create_user("Candidate")
            user_id = user.id
        adapter = _FakeAdapter([])
        with session_factory() as session:
            await JobDiscoveryService(session).discover_headhunter_vacancies(
                user_id,
                headhunter_adapter=adapter,
                locations=["Москва"],
                search_text="data engineer",
            )
        assert adapter.search_calls == [
            ("data engineer", ["Москва"]),
            ("data", ["Москва"]),
            ("engineer", ["Москва"]),
        ]

    asyncio.run(run())


def test_build_search_queries_combines_phrases_and_words_without_duplicates() -> None:
    assert build_search_queries("Data Engineer, Python; FastAPI Python") == [
        "Data Engineer, Python; FastAPI Python",
        "Data Engineer",
        "Python",
        "FastAPI Python",
        "Data",
        "Engineer",
        "FastAPI",
    ]


def test_build_search_queries_bounds_long_resume_keyword_lists_for_job_sites() -> None:
    search_text = ", ".join(f"Specialized Role {index}" for index in range(80))

    queries = build_search_queries(search_text)

    assert len(search_text) > 300
    assert 1 <= len(queries) <= 8
    assert all(len(query) <= 200 for query in queries)
    assert search_text not in queries


def test_skill_matching_normalizes_spacing_and_respects_word_boundaries() -> None:
    normalized_skill = _normalize_skill("  Machine   Learning ")

    assert normalized_skill == "machine learning"
    assert _contains_skill("Production MACHINE\nLEARNING systems", normalized_skill)
    assert _contains_skill("C++ and C# services", _normalize_skill("C++"))
    assert not _contains_skill("Django services", _normalize_skill("Go"))


def test_bounded_employment_evidence_takes_priority_over_page_noise() -> None:
    assert (
        _detect_extracted_work_format(
            title="AI Engineer",
            location="Singapore",
            description_text="Premium suggestions include hybrid opportunities.",
            employment_text="On-site · Full-time",
        )
        == "office"
    )


def test_linkedin_discovery_keeps_jobs_without_easy_apply() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            user_id = user.id
        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_linkedin_vacancies(
                user_id,
                linkedin_adapter=_FakeLinkedInAdapter(),  # type: ignore[arg-type]
                locations=[],
            )

        assert len(outcomes) == 1
        assert outcomes[0].source_url == "https://www.linkedin.com/jobs/view/2"
        assert outcomes[0].application_status == "awaiting_review"
        assert outcomes[0].vacancy_summary == "Python services"
        assert outcomes[0].work_format == "remote"
        assert outcomes[0].salary_text == "$120,000 - $150,000"
        assert outcomes[0].employment_types == ("full_time",)

    asyncio.run(run())


def test_linkedin_discovery_refreshes_stale_saved_metadata() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            vacancy = service.create_vacancy(
                source_url="https://www.linkedin.com/jobs/view/2",
                title="Backend Engineer",
                company="Example Co",
                required_skills=[],
                preferred_skills=[],
                description_text="Job search smarter with Premium",
                adapter_name="linkedin-reference",
            )
            application = service.prepare_application(user.id, vacancy.id)
            user_id = user.id
            application_id = application.id

        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_linkedin_vacancies(
                user_id,
                linkedin_adapter=_FakeLinkedInAdapter(),  # type: ignore[arg-type]
                locations=[],
            )

        assert len(outcomes) == 1
        assert outcomes[0].application_id == application_id
        assert outcomes[0].status == "already_existed"
        assert outcomes[0].vacancy_summary == "Python services"
        assert outcomes[0].work_format == "remote"
        assert outcomes[0].employment_types == ("full_time",)
        assert outcomes[0].key_skills == ("Python",)

    asyncio.run(run())


def test_linkedin_discovery_syncs_externally_submitted_status() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            user_id = user.id

        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_linkedin_vacancies(
                user_id,
                linkedin_adapter=_FakeLinkedInAdapter(application_submitted=True),
                locations=[],
            )

        assert outcomes == []
        with session_factory() as session:
            application = session.scalar(select(ApplicationRow))
            assert application is not None
            assert application.status == "submitted"

    asyncio.run(run())


def test_linkedin_discovery_syncs_exact_duplicate_status() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            duplicate = service.create_vacancy(
                source_url="https://www.linkedin.com/jobs/view/duplicate",
                title="Backend Engineer",
                company="Example Co",
                required_skills=[],
                preferred_skills=[],
                location="Remote",
                adapter_name="linkedin-reference",
            )
            duplicate_application = service.prepare_application(user.id, duplicate.id)
            user_id = user.id
            duplicate_vacancy_id = duplicate.id
            duplicate_application_id = duplicate_application.id

        with session_factory() as session:
            await JobDiscoveryService(session).discover_linkedin_vacancies(
                user_id,
                linkedin_adapter=_FakeLinkedInAdapter(application_submitted=True),
                locations=[],
            )

        with session_factory() as session:
            duplicate_application = session.get(ApplicationRow, duplicate_application_id)
            duplicate_vacancy = session.get(VacancyRow, duplicate_vacancy_id)
            assert duplicate_application is not None
            assert duplicate_vacancy is not None
            assert duplicate_application.status == "submitted"

    asyncio.run(run())


def test_linkedin_refresh_does_not_erase_saved_work_attributes() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            vacancy = service.create_vacancy(
                source_url="https://www.linkedin.com/jobs/view/2",
                title="Backend Engineer",
                company="Example Co",
                required_skills=[],
                preferred_skills=[],
                work_format="hybrid",
                employment_types=["full_time"],
            )
            service.prepare_application(user.id, vacancy.id)
            user_id = user.id

        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_linkedin_vacancies(
                user_id,
                linkedin_adapter=_EmptyMetadataLinkedInAdapter(),
                locations=[],
            )

        assert outcomes[0].work_format == "hybrid"
        assert outcomes[0].employment_types == ("full_time",)

    asyncio.run(run())


def test_greenhouse_discovery_searches_supplied_board() -> None:
    async def run() -> None:
        published_outcomes = []

        async def publish_outcome(outcome) -> None:
            published_outcomes.append(outcome)

        session_factory = _session_factory()
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            user_id = user.id
        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_greenhouse_vacancies(
                user_id,
                greenhouse_adapter=_FakeGreenhouseAdapter(),  # type: ignore[arg-type]
                board_urls=["https://boards.greenhouse.io/example"],
                locations=[],
                on_outcome=publish_outcome,
            )

        assert len(outcomes) == 1
        assert published_outcomes == outcomes
        assert outcomes[0].company == "Green Example"
        assert outcomes[0].application_status == "awaiting_review"
        assert outcomes[0].vacancy_summary == "Build Python services"
        assert outcomes[0].work_format == "hybrid"
        assert outcomes[0].salary_text == "$140,000"
        assert outcomes[0].employment_types == ("contract",)

    asyncio.run(run())


def test_headhunter_backfills_limit_after_rejected_candidate() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        rejected_url = "https://hh.ru/vacancy/old"
        new_url = "https://hh.ru/vacancy/new"
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            _create_rejected_application(
                session,
                user_id=user.id,
                source_url=rejected_url,
                adapter_name="headhunter",
            )
            user_id = user.id

        adapter = _FakeAdapter(
            [
                HeadHunterSearchHit("old", rejected_url, "Old", "Old Example"),
                HeadHunterSearchHit("new", new_url, "New", "Example"),
            ]
        )
        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_headhunter_vacancies(
                user_id, headhunter_adapter=adapter, locations=[], limit=1
            )

        assert [outcome.source_url for outcome in outcomes] == [new_url]

    asyncio.run(run())


def test_linkedin_backfills_limit_after_rejected_candidate() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        rejected_url = "https://www.linkedin.com/jobs/view/old"
        new_url = "https://www.linkedin.com/jobs/view/new"
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            _create_rejected_application(
                session,
                user_id=user.id,
                source_url=rejected_url,
                adapter_name="linkedin-reference",
            )
            user_id = user.id

        adapter = _MultiLinkedInAdapter(
            [
                LinkedInSearchHit("old", rejected_url, "Old", "Old Example"),
                LinkedInSearchHit("new", new_url, "New", "Example"),
            ]
        )
        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_linkedin_vacancies(
                user_id, linkedin_adapter=adapter, locations=[], limit=1
            )

        assert [outcome.source_url for outcome in outcomes] == [new_url]

    asyncio.run(run())


def test_greenhouse_backfills_limit_after_rejected_candidate() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        rejected_url = "https://boards.greenhouse.io/example/jobs/3"
        new_url = "https://boards.greenhouse.io/example/jobs/4"
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="", is_verified=True
            )
            _create_rejected_application(
                session,
                user_id=user.id,
                source_url=rejected_url,
                adapter_name="greenhouse",
            )
            user_id = user.id

        adapter = _MultiGreenhouseAdapter(
            (
                GreenhouseSearchHit(
                    rejected_url, "Old Python Engineer", "Old Example", "Remote", "Python"
                ),
                GreenhouseSearchHit(new_url, "New Python Engineer", "Example", "Remote", "Python"),
            )
        )
        with session_factory() as session:
            outcomes = await JobDiscoveryService(session).discover_greenhouse_vacancies(
                user_id,
                greenhouse_adapter=adapter,
                board_urls=["https://boards.greenhouse.io/example"],
                locations=[],
                limit=1,
            )

        assert [outcome.source_url for outcome in outcomes] == [new_url]

    asyncio.run(run())
