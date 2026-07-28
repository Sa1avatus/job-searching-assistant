import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.llm.router import ModelRequest
from app.services.materials_generation import (
    MaterialsDraft,
    MaterialsGenerationService,
    MaterialsLanguageMismatchError,
    NoVerifiedFactsError,
    detect_vacancy_language,
)
from app.services.recruitment import RecruitmentService
from app.storage.database import Base
from app.storage.tables import ApplicationRow, VacancyRow


class _FakeRouter:
    def __init__(self, draft: MaterialsDraft) -> None:
        self.draft = draft
        self.last_request: ModelRequest | None = None

    async def route(self, request: ModelRequest, schema: type[MaterialsDraft]) -> MaterialsDraft:
        self.last_request = request
        return self.draft


class _SequentialRouter:
    def __init__(self, *drafts: MaterialsDraft) -> None:
        self._drafts = iter(drafts)
        self.requests: list[ModelRequest] = []

    async def route(self, request: ModelRequest, schema: type[MaterialsDraft]) -> MaterialsDraft:
        self.requests.append(request)
        return next(self._drafts)


def test_english_title_overrides_russian_page_interface_noise() -> None:
    vacancy = VacancyRow(
        source_url="https://example.test/jobs/english",
        title="Solution Architect (Engineer + Product + Architecture)",
        company="Example",
        description_text=(
            "Описание служебных элементов страницы на русском языке. "
            "The actual role builds cloud platforms and distributed systems."
        ),
    )

    assert detect_vacancy_language(vacancy) == "en"


def test_russian_title_overrides_english_page_interface_noise() -> None:
    vacancy = VacancyRow(
        source_url="https://example.test/jobs/russian",
        title="Архитектор корпоративных решений",
        company="Example",
        description_text="Premium recommendations and navigation text in English.",
    )

    assert detect_vacancy_language(vacancy) == "ru"


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_application(
    session_factory,
    *,
    with_verified_fact: bool = True,
    title: str = "Backend Engineer",
    description_text: str = "Build things.",
):
    with session_factory() as session:
        service = RecruitmentService(session)
        user = service.create_user("Candidate")
        if with_verified_fact:
            service.add_profile_fact(
                user.id, category="skill", name="Python", value="5 years", is_verified=True
            )
        vacancy = service.create_vacancy(
            source_url="https://example.test/vacancy/1",
            title=title,
            company="Example Co",
            required_skills=["Python"],
            preferred_skills=[],
            description_text=description_text,
            application_fields=[
                {
                    "field_id": "why_interested",
                    "label": "Why do you want this role?",
                    "field_type": "textarea",
                    "is_required": False,
                    "semantic_category": "custom",
                },
                {
                    "field_id": "work_auth",
                    "label": "Are you authorized to work here?",
                    "field_type": "text",
                    "is_required": True,
                    "semantic_category": "work_authorization",
                },
            ],
        )
        application = service.prepare_application(user.id, vacancy.id)
        return application.id


def _save_cover_letter(session_factory, application_id: str, cover_letter_text: str) -> None:
    with session_factory() as session:
        RecruitmentService(session).update_application_materials(
            application_id,
            cover_letter_text=cover_letter_text,
            screening_answers={},
        )


def _load_cover_letter(session_factory, application_id: str) -> str:
    with session_factory() as session:
        application = session.get(ApplicationRow, application_id)
        assert application is not None
        return application.cover_letter_text


def test_material_refresh_detects_blank_and_wrong_language_cover_letters() -> None:
    session_factory = _session_factory()
    application_id = _seed_application(
        session_factory,
        title="Python-разработчик",
        description_text="Разработка внутренних сервисов и автоматизация процессов.",
    )
    router = _FakeRouter(MaterialsDraft(cover_letter_text="unused"))

    with session_factory() as session:
        service = MaterialsGenerationService(session, router)
        assert service.needs_material_refresh(application_id)

    _save_cover_letter(
        session_factory,
        application_id,
        "Dear Hiring Manager, my Python experience fits this role.",
    )
    with session_factory() as session:
        assert MaterialsGenerationService(session, router).needs_material_refresh(application_id)


def test_material_refresh_skips_non_review_application() -> None:
    session_factory = _session_factory()
    application_id = _seed_application(session_factory)
    router = _FakeRouter(MaterialsDraft(cover_letter_text="unused"))
    with session_factory() as session:
        application = session.get(ApplicationRow, application_id)
        assert application is not None
        application.status = "submitted"
        session.commit()
        assert not MaterialsGenerationService(session, router).needs_material_refresh(
            application_id
        )


def test_draft_materials_fills_open_field_and_cover_letter() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory)
        router = _FakeRouter(
            MaterialsDraft(
                cover_letter_text="I would love to join Example Co.",
                screening_answers={"why_interested": "I love backend systems."},
            )
        )
        with session_factory() as session:
            result = await MaterialsGenerationService(session, router).draft_materials(
                application_id
            )
        assert result.cover_letter_text == "I would love to join Example Co."
        assert result.filled_field_ids == ("why_interested",)
        assert router.last_request is not None
        assert "work_auth" not in router.last_request.prompt

        with session_factory() as session:
            from app.storage.tables import ApplicationRow

            application = session.get(ApplicationRow, application_id)
            answers = {a.field_id: a for a in application.answers}
            assert answers["why_interested"].answer == "I love backend systems."
            assert answers["why_interested"].answer_source == "llm_generated"
            assert answers["work_auth"].answer is None  # never touched

    asyncio.run(run())


def test_draft_materials_ignores_hallucinated_sensitive_answer() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory)
        router = _FakeRouter(
            MaterialsDraft(
                cover_letter_text="Cover letter.",
                # The model should never be asked about this field, but even if it answers it
                # anyway, the service must not write it.
                screening_answers={"work_auth": "Yes, I am authorized."},
            )
        )
        with session_factory() as session:
            result = await MaterialsGenerationService(session, router).draft_materials(
                application_id
            )
        assert result.skipped_sensitive_field_ids == ("work_auth",)

        with session_factory() as session:
            from app.storage.tables import ApplicationRow

            application = session.get(ApplicationRow, application_id)
            answers = {a.field_id: a for a in application.answers}
            assert answers["work_auth"].answer is None

    asyncio.run(run())


def test_draft_materials_does_not_overwrite_existing_answer() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory)
        with session_factory() as session:
            RecruitmentService(session).update_application_materials(
                application_id,
                cover_letter_text="My own hand-written letter.",
                screening_answers={"why_interested": "My own answer."},
            )
        router = _FakeRouter(
            MaterialsDraft(
                cover_letter_text="AI letter that should be ignored.",
                screening_answers={"why_interested": "AI answer that should be ignored."},
            )
        )
        with session_factory() as session:
            result = await MaterialsGenerationService(session, router).draft_materials(
                application_id
            )
        assert result.cover_letter_text == "My own hand-written letter."
        assert result.filled_field_ids == ()

        with session_factory() as session:
            from app.storage.tables import ApplicationRow

            application = session.get(ApplicationRow, application_id)
            answer = next(a for a in application.answers if a.field_id == "why_interested")
            assert answer.answer == "My own answer."
            assert answer.answer_source == "human_review"

    asyncio.run(run())


def test_draft_materials_requires_verified_facts() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, with_verified_fact=False)
        router = _FakeRouter(MaterialsDraft(cover_letter_text="x"))
        with session_factory() as session:
            try:
                await MaterialsGenerationService(session, router).draft_materials(application_id)
            except NoVerifiedFactsError:
                return
        raise AssertionError("expected NoVerifiedFactsError")

    asyncio.run(run())


def test_draft_materials_uses_russian_for_russian_vacancy() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(
            session_factory,
            title="Python-разработчик",
            description_text="Разработка внутренних сервисов и автоматизация процессов.",
        )
        router = _FakeRouter(
            MaterialsDraft(
                cover_letter_text=(
                    "Здравствуйте! Мой опыт разработки на Python соответствует этой позиции."
                )
            )
        )
        with session_factory() as session:
            result = await MaterialsGenerationService(session, router).draft_materials(
                application_id
            )

        assert "Здравствуйте" in result.cover_letter_text
        assert router.last_request is not None
        assert "entirely in Russian" in router.last_request.prompt

    asyncio.run(run())


def test_draft_materials_retries_english_draft_when_first_response_is_russian() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory)
        router = _SequentialRouter(
            MaterialsDraft(cover_letter_text="Здравствуйте! Я хочу работать в вашей компании."),
            MaterialsDraft(
                cover_letter_text="Dear Hiring Manager, my Python experience fits this role."
            ),
        )

        with session_factory() as session:
            generated = await MaterialsGenerationService(session, router).draft_materials(
                application_id
            )

        assert generated.cover_letter_text.startswith("Dear Hiring Manager")
        assert len(router.requests) == 2
        assert "entirely in English" in router.requests[-1].prompt

    asyncio.run(run())


def test_draft_materials_rejects_second_wrong_language_english_response() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory)
        router = _SequentialRouter(
            MaterialsDraft(cover_letter_text="Здравствуйте! Я хочу работать в вашей компании."),
            MaterialsDraft(cover_letter_text="Добрый день! Это повторный русский ответ."),
        )

        with session_factory() as session:
            try:
                await MaterialsGenerationService(session, router).draft_materials(application_id)
            except MaterialsLanguageMismatchError:
                assert len(router.requests) == 2
                return
        raise AssertionError("expected MaterialsLanguageMismatchError")

    asyncio.run(run())


def test_draft_materials_replaces_english_letter_for_russian_vacancy_when_requested() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(
            session_factory,
            title="Python-разработчик",
            description_text="Разработка внутренних сервисов и автоматизация процессов.",
        )
        _save_cover_letter(
            session_factory,
            application_id,
            "Dear Hiring Manager, my Python experience fits this role.",
        )
        router = _FakeRouter(
            MaterialsDraft(
                cover_letter_text=(
                    "Здравствуйте! Мой опыт разработки на Python соответствует этой позиции."
                )
            )
        )

        with session_factory() as session:
            generated = await MaterialsGenerationService(session, router).draft_materials(
                application_id, replace_mismatched_cover_letter=True
            )

        assert generated.cover_letter_text.startswith("Здравствуйте")

    asyncio.run(run())


def test_draft_materials_replaces_russian_letter_for_english_vacancy_when_requested() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory)
        _save_cover_letter(
            session_factory,
            application_id,
            "Здравствуйте! Мой опыт разработки на Python соответствует этой позиции.",
        )
        router = _FakeRouter(
            MaterialsDraft(
                cover_letter_text="Dear Hiring Manager, my Python experience fits this role."
            )
        )

        with session_factory() as session:
            generated = await MaterialsGenerationService(session, router).draft_materials(
                application_id, replace_mismatched_cover_letter=True
            )

        assert generated.cover_letter_text.startswith("Dear Hiring Manager")

    asyncio.run(run())


def test_draft_materials_preserves_compatible_russian_letter_when_replacement_requested() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(
            session_factory,
            title="Python-разработчик",
            description_text="Разработка внутренних сервисов и автоматизация процессов.",
        )
        original_letter = (
            "Здравствуйте! Мой собственный опыт разработки на Python подходит этой позиции."
        )
        _save_cover_letter(session_factory, application_id, original_letter)
        router = _FakeRouter(
            MaterialsDraft(
                cover_letter_text="Добрый день! Это новый автоматически созданный русский текст."
            )
        )

        with session_factory() as session:
            generated = await MaterialsGenerationService(session, router).draft_materials(
                application_id, replace_mismatched_cover_letter=True
            )

        assert generated.cover_letter_text == original_letter

    asyncio.run(run())


def test_draft_materials_preserves_compatible_english_letter_when_replacement_requested() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory)
        original_letter = "Dear Hiring Manager, this is my own English cover letter."
        _save_cover_letter(session_factory, application_id, original_letter)
        router = _FakeRouter(
            MaterialsDraft(
                cover_letter_text="Hello, this is a newly generated English cover letter."
            )
        )

        with session_factory() as session:
            generated = await MaterialsGenerationService(session, router).draft_materials(
                application_id, replace_mismatched_cover_letter=True
            )

        assert generated.cover_letter_text == original_letter

    asyncio.run(run())


def test_draft_materials_preserves_mismatched_letter_by_default() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory)
        original_letter = (
            "Здравствуйте! Мой собственный опыт разработки на Python подходит этой позиции."
        )
        _save_cover_letter(session_factory, application_id, original_letter)
        router = _FakeRouter(
            MaterialsDraft(
                cover_letter_text="Dear Hiring Manager, my Python experience fits this role."
            )
        )

        with session_factory() as session:
            generated = await MaterialsGenerationService(session, router).draft_materials(
                application_id
            )

        assert generated.cover_letter_text == original_letter

    asyncio.run(run())


def test_draft_materials_preserves_original_letter_when_language_correction_fails() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory)
        original_letter = (
            "Здравствуйте! Мой собственный опыт разработки на Python подходит этой позиции."
        )
        _save_cover_letter(session_factory, application_id, original_letter)
        router = _SequentialRouter(
            MaterialsDraft(cover_letter_text="Добрый день! Это снова русский текст."),
            MaterialsDraft(cover_letter_text="Здравствуйте! Исправление всё ещё на русском."),
        )

        with session_factory() as session:
            try:
                await MaterialsGenerationService(session, router).draft_materials(
                    application_id, replace_mismatched_cover_letter=True
                )
            except MaterialsLanguageMismatchError:
                pass
            else:
                raise AssertionError("expected MaterialsLanguageMismatchError")

        assert _load_cover_letter(session_factory, application_id) == original_letter

    asyncio.run(run())
