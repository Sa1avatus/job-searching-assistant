from dataclasses import dataclass
from enum import StrEnum


class FailureCategory(StrEnum):
    TRANSIENT_NETWORK_ERROR = "transient_network_error"
    AUTHENTICATION_FAILURE = "authentication_failure"
    SELECTOR_FAILURE = "selector_failure"
    VALIDATION_ERROR = "validation_error"
    WEBSITE_REDESIGN = "website_redesign"
    MODEL_FAILURE = "model_failure"
    PROVIDER_FAILURE = "provider_failure"
    CORRUPTED_SESSION = "corrupted_session"
    UNSUPPORTED_WORKFLOW = "unsupported_workflow"
    POLICY_RESTRICTION = "policy_restriction"
    USER_INPUT_REQUIRED = "user_input_required"
    INTERNAL_DEFECT = "internal_defect"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    should_retry: bool
    retry_delay_seconds: float | None
    next_state: str


def decide_recovery(category: FailureCategory, attempt_number: int) -> RecoveryDecision:
    if attempt_number < 1:
        raise ValueError("attempt_number must be positive")
    if category in {
        FailureCategory.TRANSIENT_NETWORK_ERROR,
        FailureCategory.PROVIDER_FAILURE,
    }:
        delay_seconds = min(2 ** (attempt_number - 1), 300)
        return RecoveryDecision(True, float(delay_seconds), "retry_scheduled")
    if category in {
        FailureCategory.AUTHENTICATION_FAILURE,
        FailureCategory.POLICY_RESTRICTION,
        FailureCategory.USER_INPUT_REQUIRED,
    }:
        return RecoveryDecision(False, None, "waiting_for_user")
    return RecoveryDecision(False, None, "failed")
