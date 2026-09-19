"""Status changes and the submission ledger: the only sanctioned ways to move an application.

* ``change_status`` validates against the lifecycle table, records an auditable timeline event
  and treats an unchanged status as a no-op (idempotent).
* ``SubmissionLedger`` makes real submission crash-safe and idempotent. A submission is
  *started* (a row in ``attempting``) before the browser touches the site and *resolved* after
  it. If a worker dies in between, the row stays ``attempting`` and the next run must verify
  what happened instead of blindly submitting again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.application_lifecycle import (
    Actor,
    IllegalApplicationTransition,
    check_transition,
)
from app.services.application_timeline import ApplicationTimelineService
from app.storage.tables import ApplicationRow, ApplicationSubmissionRow

STATE_ATTEMPTING = "attempting"
STATE_UNKNOWN = "unknown"
STATE_CONFIRMED = "confirmed"
STATE_FAILED = "failed"
OPEN_STATES = (STATE_ATTEMPTING, STATE_UNKNOWN, STATE_CONFIRMED)


def change_status(
    session: Session,
    application: ApplicationRow,
    target: str,
    *,
    actor: Actor = "human",
    source: str = "system",
    detail: dict[str, object] | None = None,
) -> bool:
    """Apply a lifecycle-checked status change and record it. Returns False for a no-op.

    Raises IllegalApplicationTransition when the lifecycle forbids the change. The caller
    commits.
    """
    previous = application.status
    if not check_transition(previous, target, actor):
        return False
    application.status = target
    timeline = ApplicationTimelineService(session)
    event = timeline.record_status_change(application.id, previous, target, source=source)
    if detail:
        event.detail_json = detail
    return True


def mark_submitted_from_site(
    session: Session,
    application: ApplicationRow,
    site_key: str,
    *,
    source: str,
    verified_by: str = "probe",
) -> bool:
    """The site itself shows the application was submitted: move to submitted and remember it.

    Idempotent, and never raises for a status the lifecycle refuses (e.g. an application the
    user already skipped): that case is left unchanged and reported as False.
    """
    try:
        change_status(session, application, "submitted", actor="system", source=source)
    except IllegalApplicationTransition:
        return False
    row = SubmissionLedger(session).open_row(application.id)
    if row is None:
        session.add(
            ApplicationSubmissionRow(
                application_id=application.id,
                user_id=application.user_id,
                site_key=site_key,
                state=STATE_CONFIRMED,
                verified_by=verified_by,
                evidence=[f"site shows the application as submitted ({source})"],
            )
        )
    elif row.state != STATE_CONFIRMED:
        SubmissionLedger(session).confirm(
            row, verified_by=verified_by, evidence=[f"site shows it as submitted ({source})"]
        )
    return True


@dataclass(frozen=True, slots=True)
class SubmissionDecision:
    """What a worker must do before touching the site."""

    action: str  # proceed | already_submitted | verify_first | wait_for_human
    row: ApplicationSubmissionRow | None = None


class SubmissionLedger:
    def __init__(self, session: Session) -> None:
        self._session = session

    def open_row(self, application_id: str) -> ApplicationSubmissionRow | None:
        return self._session.scalar(
            select(ApplicationSubmissionRow).where(
                ApplicationSubmissionRow.application_id == application_id,
                ApplicationSubmissionRow.state.in_(OPEN_STATES),
            )
        )

    def history(self, application_id: str) -> list[ApplicationSubmissionRow]:
        return list(
            self._session.scalars(
                select(ApplicationSubmissionRow)
                .where(ApplicationSubmissionRow.application_id == application_id)
                .order_by(ApplicationSubmissionRow.created_at, ApplicationSubmissionRow.id)
            )
        )

    def guard_new_submission(self, application_id: str) -> None:
        """Raise SubmissionBlocked when an open or confirmed row already exists."""
        row = self.open_row(application_id)
        if row is None:
            return
        messages = {
            STATE_CONFIRMED: "This application was already submitted.",
            STATE_ATTEMPTING: "A submission for this application is already in progress.",
            STATE_UNKNOWN: (
                "A previous submission attempt has an unknown outcome; "
                "verify it before submitting again."
            ),
        }
        raise SubmissionBlocked(messages[row.state], row.state)

    def begin(self, application: ApplicationRow, site_key: str) -> SubmissionDecision:
        """Decide whether a worker may submit now, and record the attempt if it may.

        proceed          a fresh ``attempting`` row was written; go ahead
        already_submitted a confirmed row exists; do not touch the site
        verify_first     an earlier attempt died or was inconclusive; probe the site, never resubmit
        """
        existing = self.open_row(application.id)
        if existing is not None:
            if existing.state == STATE_CONFIRMED:
                return SubmissionDecision("already_submitted", existing)
            return SubmissionDecision("verify_first", existing)
        row = ApplicationSubmissionRow(
            application_id=application.id,
            user_id=application.user_id,
            site_key=site_key,
            state=STATE_ATTEMPTING,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:
            # a concurrent worker won the race: treat its row as the truth
            winner = self.open_row(application.id)
            if winner is None:
                raise
            return SubmissionDecision(
                "already_submitted" if winner.state == STATE_CONFIRMED else "verify_first",
                winner,
            )
        return SubmissionDecision("proceed", row)

    def _resolve(
        self,
        row: ApplicationSubmissionRow,
        state: str,
        *,
        verified_by: str | None,
        evidence: list[str] | None,
        detail: str | None,
    ) -> ApplicationSubmissionRow:
        row.state = state
        row.verified_by = verified_by
        row.evidence = [*(row.evidence or []), *(evidence or [])]
        row.detail = detail
        row.updated_at = datetime.now(UTC)
        return row

    def confirm(
        self,
        row: ApplicationSubmissionRow,
        *,
        verified_by: str,
        evidence: list[str] | None = None,
        detail: str | None = None,
    ) -> ApplicationSubmissionRow:
        return self._resolve(
            row, STATE_CONFIRMED, verified_by=verified_by, evidence=evidence, detail=detail
        )

    def mark_unknown(
        self, row: ApplicationSubmissionRow, *, detail: str, evidence: list[str] | None = None
    ) -> ApplicationSubmissionRow:
        return self._resolve(row, STATE_UNKNOWN, verified_by=None, evidence=evidence, detail=detail)

    def mark_failed(
        self, row: ApplicationSubmissionRow, *, detail: str, evidence: list[str] | None = None
    ) -> ApplicationSubmissionRow:
        """Definitely not submitted: frees the application for a safe retry."""
        return self._resolve(row, STATE_FAILED, verified_by=None, evidence=evidence, detail=detail)

    def record_manual_submission(self, application: ApplicationRow, site_key: str) -> None:
        """A person (or a sync probe) says it was submitted outside the worker."""
        row = self.open_row(application.id)
        if row is None:
            row = ApplicationSubmissionRow(
                application_id=application.id,
                user_id=application.user_id,
                site_key=site_key,
                state=STATE_CONFIRMED,
                verified_by="manual",
                evidence=["marked as submitted"],
            )
            self._session.add(row)
        elif row.state != STATE_CONFIRMED:
            self.confirm(row, verified_by="manual", evidence=["marked as submitted"])

    def resolve_unknown(
        self, application_id: str, *, submitted: bool, note: str | None = None
    ) -> ApplicationSubmissionRow:
        """Human answer for an unknown/stuck attempt: it did go through, or it did not."""
        row = self.open_row(application_id)
        if row is None or row.state == STATE_CONFIRMED:
            raise SubmissionBlocked("There is no unresolved submission to resolve.", "none")
        if submitted:
            return self.confirm(
                row, verified_by="human", evidence=["human confirmed the submission"], detail=note
            )
        return self.mark_failed(row, detail=note or "human confirmed the submission did not happen")


class SubmissionBlocked(ValueError):
    """A new real submission is refused (maps to HTTP 409)."""

    def __init__(self, message: str, state: str) -> None:
        super().__init__(message)
        self.state = state
