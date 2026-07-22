import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from adapters.job_boards.browser_apply_common import CaptchaChallenge
from adapters.job_boards.headhunter_browser import ExtractedHeadHunterVacancy, HeadHunterSearchHit
from app.domain.forms import FormField, FormFieldType
from app.services.job_discovery import JobDiscoveryService, NoSearchKeywordsError
from app.services.recruitment import RecruitmentService
from app.storage.database import Base


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
                user_id, headhunter_adapter=adapter, locations=["Москва"], search_text="data engineer"
            )
        assert adapter.search_calls == [("data engineer", ["Москва"])]

    asyncio.run(run())
