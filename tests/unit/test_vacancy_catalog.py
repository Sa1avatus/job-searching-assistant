from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
    assert filtered.total == 1
    assert filtered.items[0].title == "Python Engineer"
    assert filtered.items[0].source == "headhunter"
