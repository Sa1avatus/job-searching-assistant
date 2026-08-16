from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import TaskState
from app.matching.jobs import MATCHING_QUEUE_NAME
from app.storage.tables import (
    ApplicationMatchResultRow,
    TaskTransitionRow,
    WorkflowTaskRow,
)
from app.workflows.task import ALLOWED_TRANSITIONS, InvalidTaskTransition

# The durable "stop" switch for the matching queue. The matching worker checks this
# key before claiming a new task, so pausing it halts consumption without touching
# the worker container or the in-flight task that is already running.
MATCHING_QUEUE_PAUSE_KEY = "recruitment:matching:queue:paused"

_ACTIVE_STATES = frozenset(
    {
        TaskState.PENDING.value,
        TaskState.SCHEDULED.value,
        TaskState.RETRY_SCHEDULED.value,
        TaskState.RUNNING.value,
    }
)


@dataclass(frozen=True, slots=True)
class ClearMatchingQueueReport:
    cancelled: int
    interrupted: int


async def matching_queue_is_paused(redis_client: Any) -> bool:
    value = await redis_client.get(MATCHING_QUEUE_PAUSE_KEY)
    return value in ("1", b"1", 1, True)


async def set_matching_queue_paused(redis_client: Any, *, paused: bool) -> None:
    if paused:
        await redis_client.set(MATCHING_QUEUE_PAUSE_KEY, "1")
    else:
        await redis_client.delete(MATCHING_QUEUE_PAUSE_KEY)


def _transition(
    row: WorkflowTaskRow,
    new_state: TaskState,
    *,
    now: datetime,
    reason: str,
) -> None:
    previous_state = TaskState(row.state)
    if new_state not in ALLOWED_TRANSITIONS[previous_state]:
        raise InvalidTaskTransition(
            f"Cannot clear task from {previous_state} to {new_state}"
        )
    row.state = new_state.value
    row.updated_at = now
    row.transitions.append(
        TaskTransitionRow(
            previous_state=previous_state.value,
            new_state=new_state.value,
            reason=reason,
            worker="matching-queue-admin",
            attempt_number=row.attempt_number,
            evidence=["matching-queue:clear"],
            occurred_at=now,
        )
    )


def clear_matching_queue(
    session: Session,
    *,
    now: datetime | None = None,
) -> ClearMatchingQueueReport:
    """Cancel the matching backlog and interrupt any orphaned running tasks.

    Queued tasks (pending / scheduled / retry_scheduled) move to CANCELLED, so the
    worker will never claim them again. Running tasks are left to INTERRUPTED rather
    than CANCELLED so a crashed worker's stale-task recovery cannot reschedule them
    (recovery only targets the RUNNING state). Pending application aggregates are
    marked failed so applications do not look stuck in "processing".
    """
    clear_time = now or datetime.now(UTC)
    rows = list(
        session.scalars(
            select(WorkflowTaskRow).where(
                WorkflowTaskRow.queue_name == MATCHING_QUEUE_NAME,
                WorkflowTaskRow.state.in_(_ACTIVE_STATES),
            )
        )
    )
    cancelled = 0
    interrupted = 0
    for row in rows:
        state = TaskState(row.state)
        target = TaskState.INTERRUPTED if state is TaskState.RUNNING else TaskState.CANCELLED
        try:
            _transition(
                row,
                target,
                now=clear_time,
                reason="matching queue cleared by operator",
            )
        except InvalidTaskTransition:
            # A worker claimed the task between our SELECT and UPDATE; skip it and
            # let the worker own its outcome.
            continue
        if target is TaskState.CANCELLED:
            cancelled += 1
            if row.application_id is not None:
                aggregate = session.get(ApplicationMatchResultRow, row.application_id)
                if aggregate is not None and aggregate.status == "pending":
                    aggregate.status = "failed"
                    aggregate.failure_reason = "cancelled by matching queue clear"
        else:
            interrupted += 1
    session.commit()
    return ClearMatchingQueueReport(cancelled=cancelled, interrupted=interrupted)
