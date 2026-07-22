import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.llm.router import ModelRequest
from app.services.materials_generation import (
    MaterialsDraft,
    MaterialsGenerationService,
    NoVerifiedFactsError,
)
from app.services.recruitment import RecruitmentService
from app.storage.database import Base


class _FakeRouter:
    def __init__(self, draft: MaterialsDraft) -> None:
        self.draft = draft
        self.last_request: ModelRequest | None = None

    async def route(self, request: ModelRequest, schema: type[MaterialsDraft]) -> MaterialsDraft:
        self.last_request = request
        return self.draft


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
