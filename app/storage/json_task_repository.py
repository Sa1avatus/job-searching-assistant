from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from app.domain.models import TaskState, TaskTransition
from app.workflows.task import WorkflowTask


class JsonTaskRepository:
    """Small local repository with atomic writes for restart-safe development workflows."""

    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path

    def save(self, task: WorkflowTask) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "idempotency_key": task.idempotency_key,
            "state": task.state.value,
            "attempt_number": task.attempt_number,
            "transitions": [transition.to_dict() for transition in task.transitions],
        }
        descriptor, temporary_path_text = tempfile.mkstemp(
            dir=self._storage_path.parent,
            prefix=f".{self._storage_path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_path_text)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(self._storage_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def load(self) -> WorkflowTask:
        payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        task = WorkflowTask(
            idempotency_key=payload["idempotency_key"],
            state=TaskState(payload["state"]),
            attempt_number=payload["attempt_number"],
        )
        task.transitions.extend(
            TaskTransition(
                previous_state=TaskState(transition["previous_state"]),
                new_state=TaskState(transition["new_state"]),
                reason=transition["reason"],
                worker=transition["worker"],
                attempt_number=transition["attempt_number"],
                evidence=tuple(transition["evidence"]),
                occurred_at=datetime.fromisoformat(transition["occurred_at"]),
            )
            for transition in payload["transitions"]
        )
        return task
