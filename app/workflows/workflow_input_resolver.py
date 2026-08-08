from __future__ import annotations

from pathlib import Path
from typing import Protocol


class WorkflowExecutionInputResolver(Protocol):
    def resolve_text(self, key: str) -> str: ...

    def resolve_boolean(self, key: str) -> bool: ...

    def resolve_file(self, key: str) -> Path: ...
