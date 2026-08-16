from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AutofillUsagePolicy:
    requires_review: bool
    may_send_to_llm: bool


def evaluate_autofill_usage(*, is_sensitive: bool) -> AutofillUsagePolicy:
    if not isinstance(is_sensitive, bool):
        raise TypeError("is_sensitive must be a boolean")
    return AutofillUsagePolicy(
        requires_review=is_sensitive,
        may_send_to_llm=not is_sensitive,
    )
