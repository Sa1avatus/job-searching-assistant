import asyncio
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from adapters.job_boards.browser_apply_common import ApplyBlocked
from adapters.job_boards.headhunter_browser import HeadHunterApplyResult
from app.browser.session_store import EncryptedBrowserStateStore
from app.config import Settings
from app.domain.models import TaskState
from app.storage.database import Base
from app.storage.tables import ApplicationRow, UserRow, VacancyRow
from app.storage.task_repository import ClaimedTask
from app.workers.browser_tasks import (
    ApplicationBrowserTaskHandler,
    GreenhouseBrowserReviewHandler,
    HeadHunterApplyHandler,
    LinkedInApplyHandler,
)


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _store(tmp_path: Path) -> EncryptedBrowserStateStore:
    return EncryptedBrowserStateStore(
        tmp_path, encryption_key=Fernet.generate_key().decode("ascii"), max_state_bytes=2_097_152
    )


class _FakePlaywrightEngine:
    def __init__(self, **_kwargs: object) -> None:
        self.artifact_directory = Path(".")

    async def __aenter__(self) -> "_FakePlaywrightEngine":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def storage_state(self) -> dict[str, object]:
        return {"cookies": [], "origins": []}


def _seed_application(session_factory, *, adapter_name: str) -> str:
    with session_factory() as session:
        user = UserRow(display_name="Candidate")
        vacancy = VacancyRow(
            source_url=f"https://example.test/{adapter_name}/1",
            title="Engineer",
            company="Example",
            adapter_name=adapter_name,
        )
        session.add_all([user, vacancy])
        session.flush()
        application = ApplicationRow(
            user_id=user.id,
            vacancy_id=vacancy.id,
            status="awaiting_review",
            match_score=100,
            warnings=[],
        )
        session.add(application)
        session.commit()
        return application.id


def test_headhunter_apply_disabled_by_default(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="headhunter")
        handler = HeadHunterApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path), session_factory, _store(tmp_path)
        )
        outcome = await handler.handle(
            ClaimedTask(
                task_id="t1",
                application_id=application_id,
                idempotency_key=f"application-review:{application_id}",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "headhunter_apply"},
            )
        )
        assert outcome.state is TaskState.FAILED
        assert "APP_ENABLE_HEADHUNTER_APPLY" in outcome.reason

    asyncio.run(run_handler())


def test_linkedin_apply_disabled_by_default(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="linkedin-reference")
        handler = LinkedInApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path), session_factory, _store(tmp_path)
        )
        outcome = await handler.handle(
            ClaimedTask(
                task_id="t2",
                application_id=application_id,
                idempotency_key=f"application-review:{application_id}",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "linkedin_apply"},
            )
        )
        assert outcome.state is TaskState.FAILED
        assert "APP_ENABLE_LINKEDIN_APPLY" in outcome.reason
        with session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            assert application is not None
            assert application.status == "awaiting_review"

    asyncio.run(run_handler())


def test_headhunter_apply_waits_for_user_without_captured_session(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="headhunter")
        handler = HeadHunterApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_headhunter_apply=True),
            session_factory,
            _store(tmp_path),
        )
        outcome = await handler.handle(
            ClaimedTask(
                task_id="t3",
                application_id=application_id,
                idempotency_key=f"application-review:{application_id}",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "headhunter_apply"},
            )
        )
        assert outcome.state is TaskState.WAITING_FOR_USER
        assert outcome.human_action is not None
        assert outcome.human_action.kind == "reauthenticate"
        assert "browser_login_capture.py" in outcome.human_action.instructions
        assert "submission:false" in outcome.evidence

    asyncio.run(run_handler())


def test_headhunter_apply_rejects_wrong_adapter_vacancy(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="greenhouse")
        handler = HeadHunterApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_headhunter_apply=True),
            session_factory,
            _store(tmp_path),
        )
        outcome = await handler.handle(
            ClaimedTask(
                task_id="t4",
                application_id=application_id,
                idempotency_key=f"application-review:{application_id}",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "headhunter_apply"},
            )
        )
        assert outcome.state is TaskState.FAILED
        assert outcome.reason == "hh.ru apply inputs failed validation"

    asyncio.run(run_handler())


def test_headhunter_apply_rejects_unexpected_payload(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        handler = HeadHunterApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_headhunter_apply=True),
            session_factory,
            _store(tmp_path),
        )
        outcome = await handler.handle(
            ClaimedTask(
                task_id="t5",
                application_id=None,
                idempotency_key="application-review:t5",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "headhunter_apply", "unexpected": "field"},
            )
        )
        assert outcome.state is TaskState.FAILED
        assert outcome.reason == "hh.ru apply task payload is invalid"

    asyncio.run(run_handler())


def test_headhunter_apply_passes_exact_letter_and_marks_submitted(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="headhunter")
        expected_letter = "  Здравствуйте!\n\nТочный текст письма.  "
        with session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            assert application is not None
            application.cover_letter_text = expected_letter
            session.commit()

        received_letters: list[str | None] = []

        class RecordingAdapter:
            def __init__(self, _engine: object) -> None:
                pass

            async def apply(
                self,
                url: str,
                *,
                cover_letter: str | None,
                resume_title: str | None = None,
            ) -> HeadHunterApplyResult:
                received_letters.append(cover_letter)
                return HeadHunterApplyResult((), False, url)

        handler = HeadHunterApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_headhunter_apply=True),
            session_factory,
            _store(tmp_path),
            adapter_factory=RecordingAdapter,
        )
        with (
            mock.patch("app.workers.browser_tasks.PlaywrightEngine", _FakePlaywrightEngine),
            mock.patch(
                "app.workers.browser_tasks._restore_browser_session",
                return_value={"cookies": [], "origins": []},
            ),
            mock.patch("app.workers.browser_tasks._persist_browser_session"),
        ):
            outcome = await handler.handle(
                ClaimedTask(
                    task_id="letter-success",
                    application_id=application_id,
                    idempotency_key=f"application-review:{application_id}",
                    attempt_number=1,
                    queue_name="browser",
                    payload={"workflow": "headhunter_apply"},
                )
            )

        assert outcome.state is TaskState.COMPLETED
        assert received_letters == [expected_letter]
        with session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            assert application is not None
            assert application.status == "submitted"

    asyncio.run(run_handler())


def test_headhunter_already_applied_marks_submitted(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="headhunter")

        class AlreadyAppliedAdapter:
            def __init__(self, _engine: object) -> None:
                pass

            async def apply(
                self,
                url: str,
                *,
                cover_letter: str | None,
                resume_title: str | None = None,
            ) -> HeadHunterApplyResult:
                return HeadHunterApplyResult((), True, url)

        handler = HeadHunterApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_headhunter_apply=True),
            session_factory,
            _store(tmp_path),
            adapter_factory=AlreadyAppliedAdapter,
        )
        with (
            mock.patch("app.workers.browser_tasks.PlaywrightEngine", _FakePlaywrightEngine),
            mock.patch(
                "app.workers.browser_tasks._restore_browser_session",
                return_value={"cookies": [], "origins": []},
            ),
            mock.patch("app.workers.browser_tasks._persist_browser_session"),
        ):
            outcome = await handler.handle(
                ClaimedTask(
                    task_id="already-applied",
                    application_id=application_id,
                    idempotency_key=f"application-review:{application_id}",
                    attempt_number=1,
                    queue_name="browser",
                    payload={"workflow": "headhunter_apply"},
                )
            )

        assert outcome.state is TaskState.COMPLETED
        assert "submission:already_applied" in outcome.evidence
        with session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            assert application is not None
            assert application.status == "submitted"

    asyncio.run(run_handler())


def test_headhunter_blocked_apply_preserves_status(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="headhunter")

        class BlockedAdapter:
            def __init__(self, _engine: object) -> None:
                pass

            async def apply(
                self,
                url: str,
                *,
                cover_letter: str | None,
                resume_title: str | None = None,
            ) -> HeadHunterApplyResult:
                raise ApplyBlocked("cover letter field was not verified")

        handler = HeadHunterApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_headhunter_apply=True),
            session_factory,
            _store(tmp_path),
            adapter_factory=BlockedAdapter,
        )
        with (
            mock.patch("app.workers.browser_tasks.PlaywrightEngine", _FakePlaywrightEngine),
            mock.patch(
                "app.workers.browser_tasks._restore_browser_session",
                return_value={"cookies": [], "origins": []},
            ),
        ):
            outcome = await handler.handle(
                ClaimedTask(
                    task_id="blocked-letter",
                    application_id=application_id,
                    idempotency_key=f"application-review:{application_id}",
                    attempt_number=1,
                    queue_name="browser",
                    payload={"workflow": "headhunter_apply"},
                )
            )

        assert outcome.state is TaskState.FAILED
        with session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            assert application is not None
            assert application.status == "awaiting_review"

    asyncio.run(run_handler())


def test_linkedin_apply_waits_for_user_without_captured_session(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="linkedin-reference")
        handler = LinkedInApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_linkedin_apply=True),
            session_factory,
            _store(tmp_path),
        )

        outcome = await handler.handle(
            ClaimedTask(
                task_id="linkedin-no-session",
                application_id=application_id,
                idempotency_key=f"application-review:{application_id}",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "linkedin_apply"},
            )
        )

        assert outcome.state is TaskState.WAITING_FOR_USER
        assert outcome.human_action is not None
        assert outcome.human_action.kind == "reauthenticate"
        with session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            assert application is not None
            assert application.status == "awaiting_review"

    asyncio.run(run_handler())


def test_linkedin_apply_rejects_invalid_payload_without_changing_status(
    tmp_path: Path,
) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="linkedin-reference")
        handler = LinkedInApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_linkedin_apply=True),
            session_factory,
            _store(tmp_path),
        )

        outcome = await handler.handle(
            ClaimedTask(
                task_id="linkedin-invalid",
                application_id=application_id,
                idempotency_key=f"application-review:{application_id}",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "linkedin_apply", "unexpected": True},
            )
        )

        assert outcome.state is TaskState.FAILED
        assert outcome.reason == "LinkedIn apply task payload is invalid"
        with session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            assert application is not None
            assert application.status == "awaiting_review"

    asyncio.run(run_handler())


def test_linkedin_blocked_apply_preserves_status(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="linkedin-reference")

        class BlockedLinkedInAdapter:
            def __init__(self, _engine: object) -> None:
                pass

            async def apply(self, url: str, *, answers: dict[str, str | bool | None]) -> object:
                raise ApplyBlocked("external application is required")

        handler = LinkedInApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_linkedin_apply=True),
            session_factory,
            _store(tmp_path),
            adapter_factory=BlockedLinkedInAdapter,
        )
        with (
            mock.patch("app.workers.browser_tasks.PlaywrightEngine", _FakePlaywrightEngine),
            mock.patch(
                "app.workers.browser_tasks._restore_browser_session",
                return_value={"cookies": [], "origins": []},
            ),
        ):
            outcome = await handler.handle(
                ClaimedTask(
                    task_id="linkedin-blocked",
                    application_id=application_id,
                    idempotency_key=f"application-review:{application_id}",
                    attempt_number=1,
                    queue_name="browser",
                    payload={"workflow": "linkedin_apply"},
                )
            )

        assert outcome.state is TaskState.FAILED
        with session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            assert application is not None
            assert application.status == "awaiting_review"

    asyncio.run(run_handler())


def test_linkedin_successful_apply_marks_submitted(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        application_id = _seed_application(session_factory, adapter_name="linkedin-reference")

        class SuccessfulLinkedInAdapter:
            def __init__(self, _engine: object) -> None:
                pass

            async def apply(self, url: str, *, answers: dict[str, str | bool | None]) -> object:
                class ApplyResult:
                    confirmation_url = url

                return ApplyResult()

        handler = LinkedInApplyHandler(
            Settings(_env_file=None, artifact_directory=tmp_path, enable_linkedin_apply=True),
            session_factory,
            _store(tmp_path),
            adapter_factory=SuccessfulLinkedInAdapter,
        )
        with (
            mock.patch("app.workers.browser_tasks.PlaywrightEngine", _FakePlaywrightEngine),
            mock.patch(
                "app.workers.browser_tasks._restore_browser_session",
                return_value={"cookies": [], "origins": []},
            ),
            mock.patch("app.workers.browser_tasks._persist_browser_session"),
        ):
            outcome = await handler.handle(
                ClaimedTask(
                    task_id="linkedin-success",
                    application_id=application_id,
                    idempotency_key=f"application-review:{application_id}",
                    attempt_number=1,
                    queue_name="browser",
                    payload={"workflow": "linkedin_apply"},
                )
            )

        assert outcome.state is TaskState.COMPLETED
        assert "submission:true" in outcome.evidence
        with session_factory() as session:
            application = session.get(ApplicationRow, application_id)
            assert application is not None
            assert application.status == "submitted"

    asyncio.run(run_handler())


def test_application_browser_task_handler_routes_by_workflow(tmp_path: Path) -> None:
    async def run_handler() -> None:
        session_factory = _session_factory()
        settings = Settings(_env_file=None, artifact_directory=tmp_path)
        router = ApplicationBrowserTaskHandler(
            GreenhouseBrowserReviewHandler(settings, session_factory),
            HeadHunterApplyHandler(settings, session_factory, _store(tmp_path)),
            LinkedInApplyHandler(settings, session_factory, _store(tmp_path)),
        )
        outcome = await router.handle(
            ClaimedTask(
                task_id="t6",
                application_id=None,
                idempotency_key="application-review:t6",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "headhunter_apply"},
            )
        )
        # Disabled by default -> routed correctly to HeadHunterApplyHandler, which fails fast.
        assert outcome.state is TaskState.FAILED
        assert "APP_ENABLE_HEADHUNTER_APPLY" in outcome.reason

        unknown_outcome = await router.handle(
            ClaimedTask(
                task_id="t7",
                application_id=None,
                idempotency_key="application-review:t7",
                attempt_number=1,
                queue_name="browser",
                payload={"workflow": "unknown_workflow"},
            )
        )
        assert unknown_outcome.state is TaskState.FAILED
        assert "unknown application workflow" in unknown_outcome.reason

    asyncio.run(run_handler())
