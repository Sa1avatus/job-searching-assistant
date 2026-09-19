"""Application lifecycle, status audit and the crash-safe submission ledger (stage 4A)."""

import asyncio
from pathlib import Path
from unittest import mock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from adapters.job_boards.browser_apply_common import ApplyBlocked
from adapters.job_boards.headhunter_browser import HeadHunterApplyResult
from app.browser.session_store import EncryptedBrowserStateStore
from app.config import Settings
from app.domain.application_lifecycle import (
    HUMAN_ONLY,
    TRANSITIONS,
    IllegalApplicationTransition,
    allowed_targets,
    check_transition,
)
from app.domain.application_status import APPLICATION_STATUSES
from app.domain.models import TaskState
from app.services.application_lifecycle import (
    SubmissionBlocked,
    SubmissionLedger,
    change_status,
    mark_submitted_from_site,
)
from app.services.recruitment import DuplicateEntityError, RecruitmentService
from app.storage.database import Base
from app.storage.tables import (
    ApplicationRow,
    ApplicationSubmissionRow,
    ApplicationTimelineEventRow,
    UserRow,
    VacancyRow,
)
from app.storage.task_repository import ClaimedTask
from app.workers.browser_tasks import HeadHunterApplyHandler

# -- lifecycle table ---------------------------------------------------------


def test_every_status_has_a_transition_entry_and_targets_are_known() -> None:
    assert set(TRANSITIONS) == set(APPLICATION_STATUSES)
    for targets in TRANSITIONS.values():
        assert targets <= set(APPLICATION_STATUSES)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("submitted", "awaiting_review"),  # would prepare a duplicate submission
        ("submitted", "approved"),
        ("interview", "saved"),
        ("offer", "draft"),
        ("withdrawn", "saved"),
        ("employer_rejected", "submitted"),
        ("rejected", "submitted"),
    ],
)
def test_backward_and_dead_end_transitions_are_refused(current: str, target: str) -> None:
    with pytest.raises(IllegalApplicationTransition):
        check_transition(current, target)


def test_the_documented_flow_is_allowed_and_same_status_is_a_noop() -> None:
    for current, target in [
        ("draft", "saved"),
        ("saved", "awaiting_review"),
        ("awaiting_review", "approved"),
        ("approved", "submitted"),
        ("submitted", "interview"),
        ("interview", "offer"),
    ]:
        assert check_transition(current, target) is True
    assert check_transition("submitted", "submitted") is False


def test_unknown_statuses_are_rejected() -> None:
    with pytest.raises(IllegalApplicationTransition):
        check_transition("awaiting_review", "flying")
    with pytest.raises(IllegalApplicationTransition):
        check_transition("flying", "saved")


def test_automation_cannot_take_human_only_transitions() -> None:
    assert ("awaiting_review", "approved") in HUMAN_ONLY
    with pytest.raises(IllegalApplicationTransition, match="human"):
        check_transition("awaiting_review", "approved", actor="system")
    with pytest.raises(IllegalApplicationTransition, match="human"):
        check_transition("rejected", "saved", actor="system")
    assert check_transition("awaiting_review", "submitted", actor="system") is True
    assert "approved" not in allowed_targets("awaiting_review", actor="system")
    assert "approved" in allowed_targets("awaiting_review")


# -- database-backed behaviour -----------------------------------------------


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed(factory, adapter="headhunter", status="awaiting_review"):
    with factory() as session:
        user = UserRow(display_name="C")
        vacancy = VacancyRow(
            source_url=f"https://hh.ru/vacancy/{adapter}",
            title="T",
            company="Co",
            adapter_name=adapter,
        )
        session.add_all([user, vacancy])
        session.flush()
        application = ApplicationRow(
            user_id=user.id, vacancy_id=vacancy.id, status=status, match_score=1, warnings=[]
        )
        session.add(application)
        session.commit()
        return application.id


def test_change_status_records_an_audit_event_and_is_idempotent(db) -> None:
    application_id = _seed(db)
    with db() as session:
        application = session.get(ApplicationRow, application_id)

        assert change_status(session, application, "approved", actor="human", source="review")
        assert not change_status(session, application, "approved", actor="human", source="review")
        session.commit()

        events = session.scalars(select(ApplicationTimelineEventRow)).all()
        assert [(e.previous_value, e.new_value, e.source) for e in events] == [
            ("awaiting_review", "approved", "review")
        ]


def test_an_illegal_change_changes_nothing_and_records_nothing(db) -> None:
    application_id = _seed(db, status="submitted")
    with db() as session:
        application = session.get(ApplicationRow, application_id)

        with pytest.raises(IllegalApplicationTransition):
            change_status(session, application, "awaiting_review", actor="human")

        assert application.status == "submitted"
        assert session.scalar(select(func.count()).select_from(ApplicationTimelineEventRow)) == 0


def test_manual_status_update_validates_audits_and_blocks_a_later_real_submission(db) -> None:
    application_id = _seed(db)
    with db() as session:
        service = RecruitmentService(session)
        service.update_application_status(application_id, "submitted")

        with pytest.raises(IllegalApplicationTransition):
            service.update_application_status(application_id, "awaiting_review")
        assert SubmissionLedger(session).open_row(application_id).verified_by == "manual"
        with pytest.raises(SubmissionBlocked):
            SubmissionLedger(session).guard_new_submission(application_id)


def test_recording_an_outcome_from_awaiting_review_implies_a_submission(db) -> None:
    application_id = _seed(db)
    with db() as session:
        RecruitmentService(session).update_application_status(application_id, "employer_rejected")

        assert SubmissionLedger(session).open_row(application_id).state == "confirmed"


def test_scheduling_a_real_submission_is_refused_while_an_attempt_is_unresolved(db) -> None:
    application_id = _seed(db)
    with db() as session:
        application = session.get(ApplicationRow, application_id)
        SubmissionLedger(session).begin(application, "headhunter")  # a crashed attempt
        session.commit()

        with pytest.raises(DuplicateEntityError, match="in progress"):
            RecruitmentService(session).schedule_real_submission_apply(
                application_id,
                adapter_name="headhunter",
                workflow="headhunter_apply",
                site_key="headhunter",
            )


def test_site_verified_submission_is_recorded_once(db) -> None:
    application_id = _seed(db)
    with db() as session:
        application = session.get(ApplicationRow, application_id)

        assert mark_submitted_from_site(session, application, "headhunter", source="sync")
        assert mark_submitted_from_site(session, application, "headhunter", source="sync")
        session.commit()

        rows = session.scalars(select(ApplicationSubmissionRow)).all()
        assert [(r.state, r.verified_by) for r in rows] == [("confirmed", "probe")]


def test_site_sync_never_overrides_a_status_the_user_chose(db) -> None:
    application_id = _seed(db, status="skipped")
    with db() as session:
        application = session.get(ApplicationRow, application_id)

        assert mark_submitted_from_site(session, application, "headhunter", source="sync") is False
        assert application.status == "skipped"


# -- ledger ------------------------------------------------------------------


def test_begin_is_exclusive_and_reports_earlier_attempts(db) -> None:
    application_id = _seed(db)
    with db() as session:
        application = session.get(ApplicationRow, application_id)
        ledger = SubmissionLedger(session)

        first = ledger.begin(application, "headhunter")
        session.commit()
        second = ledger.begin(application, "headhunter")

        assert first.action == "proceed" and second.action == "verify_first"
        assert second.row.id == first.row.id


def test_the_database_forbids_two_open_rows_for_one_application(db) -> None:
    application_id = _seed(db)
    with db() as session:
        for state in ("attempting", "confirmed"):
            session.add(
                ApplicationSubmissionRow(
                    application_id=application_id, user_id="u", site_key="s", state=state
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()


def test_a_failed_attempt_frees_the_application_for_a_retry(db) -> None:
    application_id = _seed(db)
    with db() as session:
        application = session.get(ApplicationRow, application_id)
        ledger = SubmissionLedger(session)
        row = ledger.begin(application, "headhunter").row
        ledger.mark_failed(row, detail="captcha before submit")
        session.commit()

        ledger.guard_new_submission(application_id)  # no longer blocked
        assert ledger.begin(application, "headhunter").action == "proceed"
        assert len(ledger.history(application_id)) == 2


def test_humans_resolve_unknown_attempts_either_way(db) -> None:
    application_id = _seed(db)
    with db() as session:
        application = session.get(ApplicationRow, application_id)
        ledger = SubmissionLedger(session)
        ledger.mark_unknown(ledger.begin(application, "headhunter").row, detail="blocked")
        session.commit()

        assert ledger.resolve_unknown(application_id, submitted=False).state == "failed"
        ledger.begin(application, "headhunter")
        session.commit()
        assert ledger.resolve_unknown(application_id, submitted=True).state == "confirmed"
        with pytest.raises(SubmissionBlocked):
            ledger.resolve_unknown(application_id, submitted=True)  # nothing left to resolve


# -- worker: crash safety and duplicate protection ----------------------------


class _FakeEngine:
    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def storage_state(self):
        return {"cookies": [], "origins": []}


def _store(tmp_path: Path) -> EncryptedBrowserStateStore:
    return EncryptedBrowserStateStore(
        tmp_path, encryption_key=Fernet.generate_key().decode("ascii"), max_state_bytes=2_097_152
    )


def _run(factory, tmp_path, application_id, adapter_cls):
    handler = HeadHunterApplyHandler(
        Settings(_env_file=None, artifact_directory=tmp_path, enable_headhunter_apply=True),
        factory,
        _store(tmp_path),
        adapter_factory=adapter_cls,
    )
    task = ClaimedTask(
        task_id="t",
        application_id=application_id,
        idempotency_key=f"application-review:{application_id}",
        attempt_number=1,
        queue_name="browser",
        payload={"workflow": "headhunter_apply"},
    )

    async def go():
        with (
            mock.patch("app.workers.browser_tasks.PlaywrightEngine", _FakeEngine),
            mock.patch(
                "app.workers.browser_tasks._restore_browser_session",
                return_value={"cookies": [], "origins": []},
            ),
            mock.patch("app.workers.browser_tasks._persist_browser_session"),
        ):
            return await handler.handle(task)

    return asyncio.run(go())


def _adapter(*, applies=None, probe=None, raises=None):
    calls = {"apply": 0, "probe": 0}

    class Adapter:
        def __init__(self, _engine) -> None:
            pass

        async def apply(self, url, *, cover_letter, resume_title=None):
            calls["apply"] += 1
            if raises is not None:
                raise raises
            return HeadHunterApplyResult((), False, url)

        async def has_submitted_application(self, url):
            calls["probe"] += 1
            if isinstance(probe, Exception):
                raise probe
            return probe

    return Adapter, calls


def _ledger_rows(factory, application_id):
    with factory() as session:
        return [(r.state, r.verified_by) for r in SubmissionLedger(session).history(application_id)]


def test_a_successful_submission_is_recorded_as_confirmed(db, tmp_path) -> None:
    application_id = _seed(db)
    adapter, calls = _adapter()

    outcome = _run(db, tmp_path, application_id, adapter)

    assert outcome.state is TaskState.COMPLETED and calls["apply"] == 1
    assert _ledger_rows(db, application_id) == [("confirmed", "worker")]


def test_a_confirmed_submission_is_never_sent_again(db, tmp_path) -> None:
    application_id = _seed(db)
    first_adapter, _ = _adapter()
    _run(db, tmp_path, application_id, first_adapter)
    with db() as session:  # even if the status was reset by mistake, the ledger still blocks
        session.get(ApplicationRow, application_id).status = "awaiting_review"
        session.commit()
    again, calls = _adapter()

    outcome = _run(db, tmp_path, application_id, again)

    assert outcome.state is TaskState.COMPLETED
    assert "submission:already_confirmed" in outcome.evidence
    assert calls == {"apply": 0, "probe": 0}


def test_a_crashed_attempt_is_verified_and_never_resubmitted(db, tmp_path) -> None:
    application_id = _seed(db)
    with db() as session:  # the previous worker died after the ledger row was written
        SubmissionLedger(session).begin(session.get(ApplicationRow, application_id), "headhunter")
        session.commit()
    adapter, calls = _adapter(probe=True)

    outcome = _run(db, tmp_path, application_id, adapter)

    assert outcome.state is TaskState.COMPLETED
    assert calls == {"apply": 0, "probe": 1}
    assert _ledger_rows(db, application_id) == [("confirmed", "probe")]
    with db() as session:
        assert session.get(ApplicationRow, application_id).status == "submitted"


def test_an_inconclusive_probe_waits_for_a_human_instead_of_resubmitting(db, tmp_path) -> None:
    application_id = _seed(db)
    with db() as session:
        SubmissionLedger(session).begin(session.get(ApplicationRow, application_id), "headhunter")
        session.commit()

    for probe in (False, RuntimeError("navigation failed")):
        adapter, calls = _adapter(probe=probe)
        outcome = _run(db, tmp_path, application_id, adapter)

        assert outcome.state is TaskState.WAITING_FOR_USER
        assert outcome.human_action.kind == "verify_submission"
        assert "submission/resolve" in outcome.human_action.instructions
        assert calls["apply"] == 0
    assert _ledger_rows(db, application_id) == [("attempting", None)]


def test_a_blocked_apply_leaves_an_unknown_attempt_that_blocks_blind_retries(db, tmp_path) -> None:
    application_id = _seed(db)
    adapter, _ = _adapter(raises=ApplyBlocked("form changed"))

    outcome = _run(db, tmp_path, application_id, adapter)

    assert outcome.state is TaskState.FAILED and "submission:unknown" in outcome.evidence
    assert _ledger_rows(db, application_id) == [("unknown", None)]
    retry, calls = _adapter()
    second = _run(db, tmp_path, application_id, retry)
    assert second.state is TaskState.WAITING_FOR_USER and calls["apply"] == 0


def test_a_human_saying_it_did_not_go_through_allows_exactly_one_new_attempt(db, tmp_path) -> None:
    application_id = _seed(db)
    blocked, _ = _adapter(raises=ApplyBlocked("form changed"))
    _run(db, tmp_path, application_id, blocked)
    with db() as session:
        SubmissionLedger(session).resolve_unknown(application_id, submitted=False)
        session.commit()
    ok, calls = _adapter()

    outcome = _run(db, tmp_path, application_id, ok)

    assert outcome.state is TaskState.COMPLETED and calls["apply"] == 1
    assert [s for s, _ in _ledger_rows(db, application_id)] == ["failed", "confirmed"]
