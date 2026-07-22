import asyncio
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from adapters.job_boards.greenhouse import GreenhouseAdapter
from app.config import Settings
from app.domain.models import TaskState
from app.storage.database import Base
from app.storage.tables import (
    ApplicationAnswerRow,
    ApplicationRow,
    CvFileRow,
    UserRow,
    VacancyRow,
)
from app.storage.task_repository import ClaimedTask
from app.workers.browser_tasks import (
    ControlledBrowserReviewHandler,
    GreenhouseBrowserReviewHandler,
)


def claimed_task(payload: dict[str, object]) -> ClaimedTask:
    return ClaimedTask(
        task_id="controlled-task",
        application_id=None,
        idempotency_key="browser-review:controlled-task",
        attempt_number=1,
        queue_name="browser",
        payload=payload,
    )


def test_controlled_browser_handler_stops_at_review_without_submit(tmp_path: Path) -> None:
    async def run_handler() -> None:
        handler = ControlledBrowserReviewHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, browser_timeout_ms=5_000),
            Path(__file__).parents[2] / "fixtures",
        )

        outcome = await handler.handle(
            claimed_task({"workflow": "controlled_review", "fixture_name": "application"})
        )

        assert outcome.state is TaskState.WAITING_FOR_USER
        assert "submission:false" in outcome.evidence
        screenshot_evidence = next(
            evidence for evidence in outcome.evidence if evidence.startswith("screenshot:")
        )
        assert Path(screenshot_evidence.removeprefix("screenshot:")).is_file()

    asyncio.run(run_handler())


def test_controlled_browser_handler_rejects_unexpected_payload(tmp_path: Path) -> None:
    async def run_handler() -> None:
        handler = ControlledBrowserReviewHandler(
            Settings(_env_file=None, artifact_directory=tmp_path),
            Path(__file__).parents[2] / "fixtures",
        )

        outcome = await handler.handle(
            claimed_task(
                {
                    "workflow": "controlled_review",
                    "fixture_name": "application",
                    "target_url": "https://example.test/forbidden",
                }
            )
        )

        assert outcome.state is TaskState.FAILED
        assert outcome.reason == "browser task payload is invalid"

    asyncio.run(run_handler())


def test_greenhouse_browser_handler_loads_persisted_inputs_without_submit(
    tmp_path: Path,
) -> None:
    async def run_handler() -> None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        document_directory = tmp_path / "documents"
        document_directory.mkdir()
        resume_path = document_directory / "controlled.pdf"
        resume_path.write_bytes(b"%PDF-1.4\n% controlled\n")
        with session_factory() as session:
            user = UserRow(display_name="Controlled Candidate")
            vacancy = VacancyRow(
                source_url="https://job-boards.greenhouse.io/example/jobs/44444",
                title="Engineer",
                company="Example",
                required_skills=[],
                preferred_skills=[],
                adapter_name="greenhouse",
                application_fields=[
                    {
                        "field_id": "resume",
                        "field_type": "file",
                        "semantic_category": "resume",
                        "is_required": True,
                    }
                ],
            )
            session.add_all([user, vacancy])
            session.flush()
            cv_file = CvFileRow(
                user_id=user.id,
                original_filename="resume.pdf",
                storage_path=str(resume_path),
                content_type="application/pdf",
                sha256="controlled",
                size_bytes=resume_path.stat().st_size,
            )
            session.add(cv_file)
            session.flush()
            application = ApplicationRow(
                user_id=user.id,
                vacancy_id=vacancy.id,
                selected_cv_file_id=cv_file.id,
                status="awaiting_review",
                match_score=100,
                warnings=[],
                answers=[
                    ApplicationAnswerRow(
                        field_id="full-name",
                        label="Full name",
                        semantic_category="full_name",
                        is_required=True,
                        answer="Controlled Candidate",
                        answer_source="human_review",
                    ),
                    ApplicationAnswerRow(
                        field_id="email",
                        label="Email",
                        semantic_category="email",
                        is_required=True,
                        answer="candidate@example.test",
                        answer_source="profile_fact",
                    ),
                ],
            )
            session.add(application)
            session.commit()
            application_id = application.id

        fixture_url = (
            (Path(__file__).parents[2] / "fixtures" / "controlled_application.html")
            .resolve()
            .as_uri()
        )

        class FixtureGreenhouseAdapter(GreenhouseAdapter):
            def supports_url(self, url: str) -> bool:
                return url.startswith("file:")

            async def prepare_review(self, url: str, answers: Mapping[str, str | bool | None]):
                return await super().prepare_review(fixture_url, answers)

        handler = GreenhouseBrowserReviewHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, browser_timeout_ms=5_000),
            session_factory,
            adapter_factory=FixtureGreenhouseAdapter,
        )
        outcome = await handler.handle(
            ClaimedTask(
                task_id="greenhouse-controlled-task",
                application_id=application_id,
                idempotency_key=f"application-review:{application_id}",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "greenhouse_review"},
            )
        )

        assert outcome.state is TaskState.WAITING_FOR_USER
        assert "submission:false" in outcome.evidence
        screenshot_path = Path(
            next(item for item in outcome.evidence if item.startswith("screenshot:")).split(":", 1)[
                1
            ]
        )
        assert screenshot_path.is_file()

    asyncio.run(run_handler())


def test_greenhouse_browser_handler_rejects_payload_target_url(tmp_path: Path) -> None:
    async def run_handler() -> None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        handler = GreenhouseBrowserReviewHandler(
            Settings(_env_file=None, artifact_directory=tmp_path),
            sessionmaker(bind=engine, expire_on_commit=False),
        )
        outcome = await handler.handle(
            ClaimedTask(
                task_id="forbidden-target",
                application_id=None,
                idempotency_key="application-review:forbidden-target",
                attempt_number=1,
                queue_name="browser",
                payload={
                    "workflow": "greenhouse_review",
                    "target_url": "http://127.0.0.1/forbidden",
                },
            )
        )

        assert outcome.state is TaskState.FAILED
        assert outcome.reason == "Greenhouse browser task payload is invalid"
        assert outcome.evidence == ("submission:false",)

    asyncio.run(run_handler())
