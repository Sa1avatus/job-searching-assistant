from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.company_blacklist import CompanyBlacklistService
from app.services.recruitment import RecruitmentService
from app.services.vacancy_catalog import VacancyCatalogService
from app.storage.database import Base


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_catalog_filters_user_vacancies_and_paginates() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        recruitment = RecruitmentService(session)
        user = recruitment.create_user("Candidate")
        other_user = recruitment.create_user("Other")
        first_vacancy = recruitment.create_vacancy(
            source_url="https://hh.ru/vacancy/1",
            title="Python Engineer",
            company="Alpha",
            required_skills=[],
            preferred_skills=[],
            location="Москва",
            adapter_name="headhunter",
        )
        second_vacancy = recruitment.create_vacancy(
            source_url="https://www.linkedin.com/jobs/view/2",
            title="Data Engineer",
            company="Beta",
            required_skills=[],
            preferred_skills=[],
            location="Berlin",
            adapter_name="linkedin-reference",
        )
        first_application = recruitment.prepare_application(user.id, first_vacancy.id)
        second_application = recruitment.prepare_application(user.id, second_vacancy.id)
        recruitment.prepare_application(other_user.id, first_vacancy.id)
        first_application.match_score = 80
        second_application.match_score = 60
        session.commit()

        first_page = VacancyCatalogService(session).list_saved_vacancies(
            user.id, page=1, page_size=1
        )
        filtered = VacancyCatalogService(session).list_saved_vacancies(
            user.id, query="python", source="headhunter", min_match_score=70
        )

    assert first_page.total == 2
    assert first_page.total_pages == 2
    assert len(first_page.items) == 1
    assert first_page.items[0].match_score == 80
    assert first_page.items[0].vacancy_summary == ""
    assert first_page.items[0].work_format == "unspecified"
    assert filtered.total == 1
    assert filtered.items[0].title == "Python Engineer"
    assert filtered.items[0].source == "headhunter"


def test_catalog_hides_rejected_and_blacklisted_companies() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        recruitment = RecruitmentService(session)
        user = recruitment.create_user("Candidate")
        visible_vacancy = recruitment.create_vacancy(
            source_url="https://example.test/jobs/visible",
            title="Visible",
            company="Visible Co",
            required_skills=[],
            preferred_skills=[],
        )
        blocked_vacancy = recruitment.create_vacancy(
            source_url="https://example.test/jobs/blocked",
            title="Blocked",
            company="Blocked Co",
            required_skills=[],
            preferred_skills=[],
        )
        visible_application = recruitment.prepare_application(user.id, visible_vacancy.id)
        recruitment.prepare_application(user.id, blocked_vacancy.id)
        CompanyBlacklistService(session).add(user.id, "Blocked Co")

        VacancyCatalogService(session).reject_saved_vacancy(visible_application.id)
        visible_page = VacancyCatalogService(session).list_saved_vacancies(user.id)
        rejected_page = VacancyCatalogService(session).list_saved_vacancies(
            user.id, status="rejected"
        )

    assert visible_page.total == 0
    assert [item.company for item in rejected_page.items] == ["Visible Co"]


def test_catalog_filters_by_source_publication_date() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        recruitment = RecruitmentService(session)
        user = recruitment.create_user("Candidate")
        old_vacancy = recruitment.create_vacancy(
            source_url="https://example.test/jobs/old",
            title="Old",
            company="Example",
            required_skills=[],
            preferred_skills=[],
            published_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        recent_vacancy = recruitment.create_vacancy(
            source_url="https://example.test/jobs/recent",
            title="Recent",
            company="Example",
            required_skills=[],
            preferred_skills=[],
            published_at=datetime(2026, 7, 20, 15, 30, tzinfo=UTC),
        )
        unknown_vacancy = recruitment.create_vacancy(
            source_url="https://example.test/jobs/unknown",
            title="Unknown",
            company="Example",
            required_skills=[],
            preferred_skills=[],
        )
        for vacancy in (old_vacancy, recent_vacancy, unknown_vacancy):
            recruitment.prepare_application(user.id, vacancy.id)

        page = VacancyCatalogService(session).list_saved_vacancies(
            user.id,
            published_from=date(2026, 7, 1),
            published_to=date(2026, 7, 31),
        )

    assert [item.title for item in page.items] == ["Recent"]
    assert page.items[0].published_at == datetime(2026, 7, 20, 15, 30, tzinfo=UTC)


def test_saved_vacancy_includes_summary_and_work_format() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        recruitment = RecruitmentService(session)
        user = recruitment.create_user("Candidate")
        vacancy = recruitment.create_vacancy(
            source_url="https://example.test/jobs/metadata",
            title="Python Engineer",
            company="Tech Co",
            required_skills=["PostgreSQL"],
            preferred_skills=[],
            location="Remote",
            description_text="Build Python services. Join a distributed team.",
        )
        recruitment.prepare_application(user.id, vacancy.id)

        page = VacancyCatalogService(session).list_saved_vacancies(user.id)

    assert len(page.items) == 1
    item = page.items[0]
    assert item.vacancy_summary == "Build Python services."
    assert item.work_format == "remote"
    assert item.key_skills == ("PostgreSQL", "Python")


def test_saved_vacancy_work_format_hybrid() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        recruitment = RecruitmentService(session)
        user = recruitment.create_user("Candidate")
        vacancy = recruitment.create_vacancy(
            source_url="https://example.test/jobs/hybrid",
            title="Developer",
            company="Hybrid Co",
            required_skills=[],
            preferred_skills=[],
            location="",
            description_text="Гибридный формат работы в команде.",
        )
        recruitment.prepare_application(user.id, vacancy.id)

        page = VacancyCatalogService(session).list_saved_vacancies(user.id)

    assert page.items[0].work_format == "hybrid"


def test_catalog_exposes_and_filters_vacancy_attributes() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        recruitment = RecruitmentService(session)
        user = recruitment.create_user("Candidate")
        matching = recruitment.create_vacancy(
            source_url="https://example.test/jobs/remote-contract",
            title="Platform Engineer",
            company="Remote Co",
            required_skills=[],
            preferred_skills=[],
            salary_text="$120,000",
            work_format="remote",
            employment_types=["contract"],
        )
        other = recruitment.create_vacancy(
            source_url="https://example.test/jobs/office-full-time",
            title="Office Engineer",
            company="Office Co",
            required_skills=[],
            preferred_skills=[],
            work_format="office",
            employment_types=["full_time"],
        )
        recruitment.prepare_application(user.id, matching.id)
        recruitment.prepare_application(user.id, other.id)

        page = VacancyCatalogService(session).list_saved_vacancies(
            user.id,
            work_format="remote",
            employment_type="contract",
        )

    assert page.total == 1
    assert page.items[0].salary_text == "$120,000"
    assert page.items[0].work_format == "remote"
    assert page.items[0].employment_types == ("contract",)
