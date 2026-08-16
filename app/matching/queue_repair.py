from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import TaskState
from app.matching.jobs import INTERACTIVE_MATCHING_PRIORITY, MATCHING_QUEUE_NAME
from app.storage.database import SessionFactory
from app.storage.tables import ApplicationMatchResultRow, TaskTransitionRow, WorkflowTaskRow
from app.workflows.task import ALLOWED_TRANSITIONS

_ACTIVE_STATES = {
    TaskState.PENDING.value,
    TaskState.SCHEDULED.value,
    TaskState.RETRY_SCHEDULED.value,
    TaskState.RUNNING.value,
}


@dataclass(frozen=True, slots=True)
class QueueRepairReport:
    apply: bool
    active_legacy_tasks: int
    kept_task_id: str | None
    migrated: int
    cancelled: int
    interrupted: int


def repair_legacy_matching_queue(session: Session, *, apply: bool) -> QueueRepairReport:
    rows = list(
        session.scalars(
            select(WorkflowTaskRow)
            .where(
                WorkflowTaskRow.queue_name == "dispatcher",
                WorkflowTaskRow.idempotency_key.startswith("matching-v2:"),
                WorkflowTaskRow.state.in_(_ACTIVE_STATES),
            )
            .order_by(WorkflowTaskRow.created_at.desc())
        )
    )
    kept = rows[0] if rows else None
    interrupted = sum(row.state == TaskState.RUNNING.value for row in rows[1:])
    cancelled = len(rows[1:]) - interrupted
    report = QueueRepairReport(
        apply=apply,
        active_legacy_tasks=len(rows),
        kept_task_id=kept.id if kept is not None else None,
        migrated=1 if kept is not None else 0,
        cancelled=cancelled,
        interrupted=interrupted,
    )
    if not apply:
        session.rollback()
        return report

    now = datetime.now(UTC)
    for row in rows:
        if kept is not None and row.id == kept.id:
            if row.state == TaskState.RUNNING.value:
                _transition(
                    row,
                    TaskState.INTERRUPTED,
                    now=now,
                    reason="active legacy claim interrupted during matching queue migration",
                )
                _transition(
                    row,
                    TaskState.SCHEDULED,
                    now=now,
                    reason="latest matching request migrated to dedicated queue",
                )
            elif row.state == TaskState.PENDING.value:
                _transition(
                    row,
                    TaskState.SCHEDULED,
                    now=now,
                    reason="latest matching request migrated to dedicated queue",
                )
            row.queue_name = MATCHING_QUEUE_NAME
            row.priority = max(row.priority, INTERACTIVE_MATCHING_PRIORITY)
            row.scheduled_for = now
            row.updated_at = now
            continue

        target_state = (
            TaskState.INTERRUPTED if row.state == TaskState.RUNNING.value else TaskState.CANCELLED
        )
        _transition(
            row,
            target_state,
            now=now,
            reason="legacy matching backlog superseded during dedicated queue migration",
        )
        if row.application_id is not None:
            aggregate = session.get(ApplicationMatchResultRow, row.application_id)
            if aggregate is not None and aggregate.status == "pending":
                aggregate.status = "failed"
                aggregate.failure_reason = "superseded during matching queue repair"

    session.commit()
    return report


def _transition(
    row: WorkflowTaskRow,
    new_state: TaskState,
    *,
    now: datetime,
    reason: str,
) -> None:
    previous_state = TaskState(row.state)
    if new_state not in ALLOWED_TRANSITIONS[previous_state]:
        raise ValueError(f"Cannot repair task from {previous_state} to {new_state}")
    row.state = new_state.value
    row.updated_at = now
    row.transitions.append(
        TaskTransitionRow(
            previous_state=previous_state.value,
            new_state=new_state.value,
            reason=reason,
            worker="matching-queue-repair",
            attempt_number=row.attempt_number,
            evidence=["migration:dedicated-matching-queue"],
            occurred_at=now,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair the legacy matching dispatcher backlog")
    parser.add_argument("--apply", action="store_true", help="commit the repair")
    arguments = parser.parse_args()
    with SessionFactory() as session:
        report = repair_legacy_matching_queue(session, apply=arguments.apply)
    print(json.dumps(asdict(report), sort_keys=True))


if __name__ == "__main__":
    main()
