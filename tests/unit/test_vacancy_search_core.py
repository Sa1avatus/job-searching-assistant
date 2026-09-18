import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.company_blacklist import CompanyBlacklistService
from app.services.recruitment import (
    BlacklistedCompanyError,
    DuplicateEntityError,
    RecruitmentService,
)
from app.services.vacancy_catalog import VacancyCatalogService
from app.services.vacancy_identity import find_vacancy_by_identity
from app.storage.database import Base
from app.storage.tables import VacancyRow


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _vacancy(recruitment: RecruitmentService, url: str, company: str = "Acme", title: str = "Dev"):
    return recruitment.create_vacancy(
        source_url=url,
        title=title,
        company=company,
        required_skills=[],
        preferred_skills=[],
        adapter_name="headhunter" if "hh.ru" in url else "generic",
    )


def test_identity_columns_are_filled_on_insert() -> None:
    session = _session()
    vacancy = _vacancy(RecruitmentService(session), "https://spb.hh.ru/vacancy/555?query=x")

    assert (vacancy.source_key, vacancy.source_id) == ("headhunter", "555")
    assert vacancy.canonical_url == "https://hh.ru/vacancy/555"
    assert vacancy.dedup_fingerprint


def test_reimporting_the_same_posting_under_another_url_is_a_duplicate() -> None:
    session = _session()
    recruitment = RecruitmentService(session)
    original = _vacancy(recruitment, "https://hh.ru/vacancy/555")

    with pytest.raises(DuplicateEntityError):
        _vacancy(recruitment, "https://spb.hh.ru/vacancy/555?hhtmFrom=vacancy_search_list")

    assert session.scalar(select(func.count()).select_from(VacancyRow)) == 1
    found = find_vacancy_by_identity(session, "https://www.hh.ru/vacancy/555/")
    assert found is not None and found.id == original.id


def test_different_postings_are_not_conflated() -> None:
    session = _session()
    recruitment = RecruitmentService(session)
    _vacancy(recruitment, "https://hh.ru/vacancy/1")
    _vacancy(recruitment, "https://hh.ru/vacancy/2")

    assert find_vacancy_by_identity(session, "https://hh.ru/vacancy/3") is None


def test_blacklist_blocks_new_applications_and_removal_reallows_them() -> None:
    session = _session()
    recruitment = RecruitmentService(session)
    user = recruitment.create_user("Candidate")
    vacancy = _vacancy(recruitment, "https://hh.ru/vacancy/9", company="Bad Corp")
    blacklist = CompanyBlacklistService(session)
    entry = blacklist.add(user.id, "  bad   CORP ")

    with pytest.raises(BlacklistedCompanyError):
        recruitment.prepare_application(user.id, vacancy.id)

    blacklist.remove(user.id, entry.id)
    assert recruitment.prepare_application(user.id, vacancy.id).vacancy_id == vacancy.id


def test_blacklist_is_owner_scoped() -> None:
    session = _session()
    recruitment = RecruitmentService(session)
    blocker = recruitment.create_user("Blocker")
    other = recruitment.create_user("Other")
    vacancy = _vacancy(recruitment, "https://hh.ru/vacancy/9", company="Bad Corp")
    CompanyBlacklistService(session).add(blocker.id, "Bad Corp")

    assert recruitment.prepare_application(other.id, vacancy.id).user_id == other.id


def test_source_filter_uses_the_canonical_source_key() -> None:
    session = _session()
    recruitment = RecruitmentService(session)
    user = recruitment.create_user("Candidate")
    for url in (
        "https://hh.ru/vacancy/1",
        "https://www.linkedin.com/jobs/view/3912345678/",
        "https://example.test/jobs/x",
    ):
        recruitment.prepare_application(user.id, _vacancy(recruitment, url).id)

    catalog = VacancyCatalogService(session)
    assert {i.source for i in catalog.list_saved_vacancies(user.id, source="headhunter").items} == {
        "headhunter"
    }
    assert catalog.list_saved_vacancies(user.id, source="linkedin").total == 1
    assert catalog.list_saved_vacancies(user.id, source="other").total == 1
    assert catalog.list_saved_vacancies(user.id, source="all").total == 3


def test_pagination_is_deterministic_and_covers_everything_once() -> None:
    session = _session()
    recruitment = RecruitmentService(session)
    user = recruitment.create_user("Candidate")
    for index in range(7):
        application = recruitment.prepare_application(
            user.id, _vacancy(recruitment, f"https://hh.ru/vacancy/{100 + index}").id
        )
        application.match_score = 50  # identical scores force the tie-breakers to decide
    session.commit()

    catalog = VacancyCatalogService(session)

    def walk() -> list[str]:
        seen: list[str] = []
        for page in (1, 2, 3):
            seen += [
                i.application_id
                for i in catalog.list_saved_vacancies(user.id, page=page, page_size=3).items
            ]
        return seen

    first, second = walk(), walk()
    assert first == second
    assert len(first) == len(set(first)) == 7
