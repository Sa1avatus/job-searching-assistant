import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.domain.models import TaskState
from app.matching.rag_client import RagDocumentResult
from app.services.resume_rag_jobs import ResumeRagJobService
from app.storage.database import Base
from app.storage.tables import CvFileRow, UserRow, WorkflowTaskRow
from app.storage.task_repository import ClaimedTask, SqlTaskRepository
from app.workers.dispatcher import ResumeRagSyncTaskHandler


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _resume(session: Session) -> CvFileRow:
    user = UserRow(display_name="Candidate")
    session.add(user)
    session.flush()
    resume = CvFileRow(
        user_id=user.id,
        original_filename="resume.txt",
        storage_path="resume.txt",
        content_type="text/plain",
        sha256="a" * 64,
        size_bytes=10,
        skills=["Python"],
        experience_summary="Python developer",
        search_keywords="Python",
        analyzed_at=user.created_at,
    )
    session.add(resume)
    session.commit()
    return resume


def test_resume_rag_job_is_idempotent_and_manual_retry_has_a_fresh_attempt_budget() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        resume = _resume(session)
        service = ResumeRagJobService(session)
        first = service.schedule(resume.id)
        repeated = service.schedule(resume.id)
        assert repeated.id == first.id
        assert first.task_payload == {
            "cv_file_id": resume.id,
            "content_version": first.idempotency_key.split(":")[2],
        }
        assert "Python developer" not in str(first.task_payload)

        repository = SqlTaskRepository(session)
        claimed = repository.claim_next(worker="test-worker")
        assert claimed is not None
        repository.finish_claim(
            claimed.task_id,
            new_state=TaskState.FAILED,
            reason="sanitized failure",
            worker="test-worker",
            evidence=("failure_code:rag_sync_failed",),
        )

        failed = service.status(resume.id)
        assert failed.status == "failed"
        assert failed.failure_code == "rag_sync_failed"
        retry = service.schedule(resume.id, force=True)
        assert retry.id != first.id
        assert retry.state == "scheduled"
        assert retry.attempt_number == 0

        service.delete_for_cv(resume.id)
        assert session.scalars(select(WorkflowTaskRow)).all() == []


def test_resume_rag_handler_returns_durable_success_or_retry(monkeypatch) -> None:
    async def run_test() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            resume = _resume(session)

        async def successful_sync(_service, cv_file_id: str):
            return RagDocumentResult(
                document_id="document-1",
                external_document_id=f"cv:{cv_file_id}",
                version=1,
                status="indexed",
                content_hash="hash",
            )

        monkeypatch.setattr("app.workers.dispatcher.RagSyncService.sync_resume", successful_sync)
        handler = ResumeRagSyncTaskHandler(
            session_factory,
            Settings(_env_file=None, database_url="sqlite://"),
        )
        claimed = ClaimedTask(
            task_id="task-1",
            application_id=None,
            idempotency_key=f"rag-resume-sync:{resume.id}:version",
            attempt_number=1,
            queue_name="dispatcher",
            payload={"cv_file_id": resume.id, "content_version": "version"},
        )
        success = await handler.handle(claimed)
        assert success.state is TaskState.COMPLETED
        assert success.evidence == (f"resume:{resume.id}", "rag_status:indexed")

        async def failed_sync(_service, _cv_file_id: str):
            return None

        monkeypatch.setattr("app.workers.dispatcher.RagSyncService.sync_resume", failed_sync)
        retry = await handler.handle(claimed)
        assert retry.state is TaskState.RETRY_SCHEDULED
        assert retry.evidence[-1] == "failure_code:rag_sync_failed"

    asyncio.run(run_test())
