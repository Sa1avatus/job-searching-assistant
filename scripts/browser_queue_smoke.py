from __future__ import annotations

import time
import uuid
from pathlib import Path

from app.config import get_settings
from app.domain.models import TaskState
from app.storage.database import SessionFactory
from app.storage.tables import TaskTransitionRow, WorkflowTaskRow


def run() -> None:
    task = WorkflowTaskRow(
        idempotency_key=f"browser-review:{uuid.uuid4()}",
        queue_name="browser",
        task_payload={"workflow": "controlled_review", "fixture_name": "application"},
        state=TaskState.SCHEDULED.value,
        priority=1_000_000,
    )
    task.transitions.append(
        TaskTransitionRow(
            previous_state=TaskState.PENDING.value,
            new_state=TaskState.SCHEDULED.value,
            reason="controlled browser queue smoke scheduled",
            worker="smoke",
            attempt_number=0,
            evidence=["target:controlled_fixture", "submission:false"],
        )
    )
    with SessionFactory() as session:
        session.add(task)
        session.commit()
        task_id = task.id

    deadline = time.monotonic() + 60
    screenshot_path: Path | None = None
    while time.monotonic() < deadline:
        with SessionFactory() as session:
            current = session.get(WorkflowTaskRow, task_id)
            if current is None:
                raise RuntimeError("Controlled browser task disappeared")
            if current.state == TaskState.WAITING_FOR_USER.value:
                evidence = current.transitions[-1].evidence
                if "submission:false" not in evidence:
                    raise RuntimeError("Browser task lacks no-submit evidence")
                screenshot_value = next(
                    value.removeprefix("screenshot:")
                    for value in evidence
                    if value.startswith("screenshot:")
                )
                screenshot_path = Path(screenshot_value)
                if not screenshot_path.is_file():
                    raise RuntimeError("Browser task screenshot is unavailable")
                if len(current.human_actions) != 1:
                    raise RuntimeError("Browser task lacks a single review checkpoint")
                checkpoint = current.human_actions[0]
                if checkpoint.kind != "application_review" or len(checkpoint.artifacts) != 1:
                    raise RuntimeError("Browser review evidence was not registered")
                session.delete(current)
                session.commit()
                break
            if current.state in {TaskState.FAILED.value, TaskState.CANCELLED.value}:
                raise RuntimeError(f"Controlled browser task ended as {current.state}")
        time.sleep(0.5)
    else:
        raise TimeoutError("Controlled browser task did not reach the review boundary")

    artifact_root = get_settings().artifact_directory.resolve()
    resolved_screenshot = screenshot_path.resolve()
    if not resolved_screenshot.is_relative_to(artifact_root):
        raise RuntimeError("Browser task screenshot escaped the artifact root")
    resolved_screenshot.unlink(missing_ok=True)
    print("browser queue smoke: waiting_for_user, submission=false, screenshot=verified")


if __name__ == "__main__":
    run()
