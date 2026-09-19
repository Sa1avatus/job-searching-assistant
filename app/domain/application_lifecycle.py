"""Application lifecycle: one transition table for every status change.

Flow: shortlist (draft/saved) -> review (awaiting_review) -> human approval (approved) ->
submit (submitted) -> track (interview / offer / employer_rejected), with side exits
(rejected, skipped, withdrawn) and ``needs_review`` for anything an email or sync could not
classify with confidence.

Rules the table encodes:
* nothing moves backwards once submitted - a submitted application cannot return to review or
  approval, because that is how a second, duplicate submission would be prepared;
* ``withdrawn`` and ``employer_rejected`` are final (``employer_rejected`` can only be sent to
  ``needs_review`` so a misclassified email can be corrected by a human);
* a rejected or skipped shortlist entry may be restored, a submitted one may not;
* setting the same status again is a no-op, so repeated syncs and retries are idempotent.

Submission itself is guarded separately by the submission ledger
(``app/services/application_lifecycle.py``).
"""

from __future__ import annotations

from typing import Literal

from app.domain.application_status import APPLICATION_STATUSES

Actor = Literal["human", "system", "email"]

# The person may record what happened outside the app (they applied by hand and got an
# interview or a rejection), so the pre-submission statuses can jump to the tracking statuses.
_PRE_SUBMISSION = ("draft", "saved", "awaiting_review", "approved", "needs_review")
_TRACKING = frozenset({"interview", "offer", "employer_rejected"})
# Statuses that imply the application was actually sent.
SUBMISSION_IMPLIED = frozenset({"submitted", "interview", "offer", "employer_rejected"})
POST_SUBMISSION = SUBMISSION_IMPLIED | {"withdrawn"}

TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset(
        {"saved", "awaiting_review", "approved", "rejected", "skipped", "withdrawn", "submitted"}
    ),
    "saved": frozenset(
        {"draft", "awaiting_review", "approved", "rejected", "skipped", "withdrawn", "submitted"}
    ),
    "awaiting_review": frozenset(
        {"saved", "approved", "rejected", "skipped", "withdrawn", "submitted", "needs_review"}
    ),
    "approved": frozenset(
        {"awaiting_review", "saved", "rejected", "skipped", "withdrawn", "submitted"}
    ),
    "needs_review": frozenset(
        {"awaiting_review", "saved", "approved", "rejected", "skipped", "withdrawn", "submitted"}
    ),
    "submitted": frozenset(
        {"interview", "offer", "employer_rejected", "withdrawn", "needs_review"}
    ),
    "interview": frozenset({"offer", "employer_rejected", "withdrawn", "needs_review"}),
    "offer": frozenset({"employer_rejected", "withdrawn", "needs_review"}),
    "rejected": frozenset({"saved", "awaiting_review"}),
    "skipped": frozenset({"saved", "awaiting_review"}),
    "withdrawn": frozenset(),
    "employer_rejected": frozenset({"needs_review"}),
}
for _source in _PRE_SUBMISSION:
    TRANSITIONS[_source] = TRANSITIONS[_source] | _TRACKING

# Transitions only a person may make; automation (sync, email, workers) is refused.
HUMAN_ONLY: frozenset[tuple[str, str]] = frozenset(
    {(source, "approved") for source in ("draft", "saved", "awaiting_review", "needs_review")}
    | {
        (source, target)
        for source in ("rejected", "skipped")
        for target in ("saved", "awaiting_review")
    }
    | {("approved", "awaiting_review"), ("employer_rejected", "needs_review")}
)

# An acknowledgement email ("we received your application") is the one automated signal allowed
# to set ``approved``: it means the employer accepted the application into its process.
EMAIL_EXTRA: frozenset[tuple[str, str]] = frozenset(
    (source, "approved")
    for source in ("draft", "saved", "awaiting_review", "needs_review", "submitted")
) | {("needs_review", "awaiting_review")}

# Statuses from which a real (irreversible) submission may still be prepared.
SUBMITTABLE_STATUSES = frozenset({"awaiting_review"})

assert set(TRANSITIONS) == set(APPLICATION_STATUSES), "every status needs a transition entry"


class IllegalApplicationTransition(ValueError):
    """The requested status change breaks the lifecycle (maps to HTTP 409)."""

    def __init__(self, current: str, target: str, reason: str) -> None:
        super().__init__(f"Cannot move an application from {current} to {target}: {reason}")
        self.current = current
        self.target = target
        self.reason = reason


def check_transition(current: str, target: str, actor: Actor = "human") -> bool:
    """True when the change is a real transition, False when it is a no-op (same status).

    Raises IllegalApplicationTransition for anything the lifecycle does not allow.
    """
    if target not in TRANSITIONS:
        raise IllegalApplicationTransition(current, target, "unsupported status")
    if current not in TRANSITIONS:
        raise IllegalApplicationTransition(current, target, "unknown current status")
    if current == target:
        return False
    if actor == "email" and (current, target) in EMAIL_EXTRA:
        return True
    if target not in TRANSITIONS[current]:
        raise IllegalApplicationTransition(current, target, "not an allowed transition")
    if actor in ("system", "email") and (current, target) in HUMAN_ONLY:
        raise IllegalApplicationTransition(current, target, "requires an explicit human action")
    return True


def allowed_targets(current: str, actor: Actor = "human") -> list[str]:
    return sorted(
        target
        for target in TRANSITIONS.get(current, frozenset())
        if actor == "human" or (current, target) not in HUMAN_ONLY
    )
