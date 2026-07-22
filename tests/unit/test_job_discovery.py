import asyncio

from sqlalchemy import create_engine
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
    build_search_queries,
)
from app.services.recruitment import RecruitmentService
from app.storage.database import Base
from app.storage.tables import ApplicationRow, CvFileRow


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
        )


class _FakeLinkedInAdapter:
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
        )


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


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
        assert second[0].status == "already_existed"
        assert second[0].application_id == first[0].application_id

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

    asyncio.run(run())


def test_greenhouse_discovery_searches_supplied_board() -> None:
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
            outcomes = await JobDiscoveryService(session).discover_greenhouse_vacancies(
                user_id,
                greenhouse_adapter=_FakeGreenhouseAdapter(),  # type: ignore[arg-type]
                board_urls=["https://boards.greenhouse.io/example"],
                locations=[],
            )

        assert len(outcomes) == 1
        assert outcomes[0].company == "Green Example"

    asyncio.run(run())
