"""Turns the raw interaction descriptors reported by the recorder script into candidate
reach-steps (navigate/fill/click). Nothing here executes anything or stores a typed value: it
only proposes locator candidates from what the recorder observed, using the same signal
priority as form discovery (stable identifier first, free text last).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.workflow_selectors import WorkflowSelectorCandidate, WorkflowSelectorKind

_MAX_CANDIDATES = 5


@dataclass(frozen=True, slots=True)
class RecordedAction:
    """One click or completed field edit captured while a person recorded a search themselves."""

    kind: str  # "click" | "fill"
    tag: str
    element_type: str
    element_id: str
    name: str
    role: str
    aria_label: str
    test_id: str
    placeholder: str
    label_text: str
    text: str
    value_preview: str = ""
    value_length: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> RecordedAction:
        def field(key: str) -> str:
            return str(payload.get(key) or "").strip()

        value_length = payload.get("valueLength")
        return cls(
            kind=field("kind"),
            tag=field("tag"),
            element_type=field("type"),
            element_id=field("id"),
            name=field("name"),
            role=field("role"),
            aria_label=field("ariaLabel"),
            test_id=field("testId"),
            placeholder=field("placeholder"),
            label_text=field("labelText"),
            text=field("text"),
            value_preview=field("value"),
            value_length=int(value_length) if isinstance(value_length, int | float) else 0,
        )

    def description(self) -> str:
        """Short label for the review screen. Never the typed value for a click action."""
        return (
            self.label_text
            or self.aria_label
            or self.placeholder
            or self.text
            or f"{self.tag}{f'#{self.element_id}' if self.element_id else ''}"
        )


def selector_candidates_for(action: RecordedAction) -> list[WorkflowSelectorCandidate]:
    """Best-effort locator candidates, most reliable first. May be empty."""
    candidates: list[WorkflowSelectorCandidate] = []

    def add(kind: WorkflowSelectorKind, value: str) -> None:
        if value and len(candidates) < _MAX_CANDIDATES:
            candidates.append(WorkflowSelectorCandidate(kind=kind, value=value))

    add("test_id", action.test_id)
    add("id", action.element_id)
    add("label", action.label_text)
    add("name", action.name)
    add("placeholder", action.placeholder)
    if action.aria_label and action.aria_label != action.label_text:
        add("label", action.aria_label)
    add("role", action.role)
    # Buttons, links and autocomplete-suggestion rows are often just visible text with no
    # other identifying attribute at all; an exact-text match on the captured tag is still
    # far more reliable than dropping the step entirely.
    if not candidates and action.kind == "click" and action.text and action.tag:
        escaped_text = action.text.replace('"', '\\"')
        add("css", f'{action.tag}:text-is("{escaped_text}")')
    return candidates
