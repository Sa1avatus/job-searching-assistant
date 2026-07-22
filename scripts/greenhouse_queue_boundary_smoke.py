"""Prove that the live browser worker rejects arbitrary Greenhouse target URLs."""

from __future__ import annotations

import time
import uuid

from app.domain.models import TaskState
from app.storage.database import SessionFactory
from app.storage.tables import TaskTransitionRow, WorkflowTaskRow


def run() -> None:
    task = WorkflowTaskRow(
        idempotency_key=f"application-review:boundary-{uuid.uuid4()}",
        queue_name="browser",
        task_payload={
            "workflow": "greenhouse_review",
            "target_url": "http://127.0.0.1/forbidden",
        },
        state=TaskState.SCHEDULED.value,
        priority=1_000_000,
    )
    task.transitions.append(
        TaskTransitionRow(
            previous_state=TaskState.PENDING.value,
            new_state=TaskState.SCHEDULED.value,
            reason="Greenhouse browser boundary smoke scheduled",
            worker="smoke",
            attempt_number=0,
            evidence=["submission:false"],
        )
    )
    with SessionFactory() as session:
        session.add(task)
        session.commit()
        task_id = task.id

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with SessionFactory() as session:
            current = session.get(WorkflowTaskRow, task_id)
            if current is None:
                raise RuntimeError("Greenhouse boundary task disappeared")
            if current.state == TaskState.FAILED.value:
                final_transition = current.transitions[-1]
                if final_transition.reason != "Greenhouse browser task payload is invalid":
                    raise RuntimeError(f"Unexpected boundary result: {final_transition.reason}")
                if "submission:false" not in final_transition.evidence:
                    raise RuntimeError("Greenhouse boundary lacks no-submit evidence")
                session.delete(current)
                session.commit()
                print("Greenhouse queue boundary smoke passed: arbitrary URL rejected")
                return
            if current.state in {
                TaskState.COMPLETED.value,
                TaskState.WAITING_FOR_USER.value,
            }:
                raise RuntimeError(f"Unsafe Greenhouse boundary state: {current.state}")
        time.sleep(0.25)
    raise TimeoutError("Greenhouse boundary task was not handled")


if __name__ == "__main__":
    run()
