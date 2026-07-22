import asyncio
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.browser.engine import PlaywrightEngine
from app.domain.models import TaskState
from app.storage.database import Base
from app.storage.task_repository import SqlTaskRepository
from app.workflows.application_review import ApplicationReviewWorkflow


def test_controlled_application_reaches_durable_review_checkpoint(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    fixture_path = Path(__file__).parents[1] / "fixtures" / "application.html"
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text("Controlled test resume", encoding="utf-8")

    async def run_workflow() -> None:
        with Session(engine) as session:
            repository = SqlTaskRepository(session)
            async with PlaywrightEngine(artifact_directory=tmp_path / "artifacts") as browser:
                workflow = ApplicationReviewWorkflow(repository, browser)
                actions = await workflow.run(
                    idempotency_key="application:controlled:1",
                    application_url=fixture_path.resolve().as_uri(),
                    full_name="Test Candidate",
                    email="candidate@example.test",
                    resume_path=resume_path,
                )
                assert all(action.is_successful for action in actions)

    asyncio.run(run_workflow())

    with Session(engine) as restarted_session:
        restored = SqlTaskRepository(restarted_session).get_or_create("application:controlled:1")
        assert restored.state is TaskState.WAITING_FOR_USER
        assert restored.attempt_number == 1
        assert Path(restored.transitions[-1].evidence[0]).exists()
