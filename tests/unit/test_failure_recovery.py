from app.domain.failures import FailureCategory, decide_recovery


def test_transient_failure_uses_bounded_exponential_backoff() -> None:
    first = decide_recovery(FailureCategory.TRANSIENT_NETWORK_ERROR, 1)
    tenth = decide_recovery(FailureCategory.TRANSIENT_NETWORK_ERROR, 10)

    assert first.should_retry is True
    assert first.retry_delay_seconds == 1
    assert tenth.retry_delay_seconds == 300


def test_policy_and_auth_failures_never_retry_blindly() -> None:
    for category in (
        FailureCategory.AUTHENTICATION_FAILURE,
        FailureCategory.POLICY_RESTRICTION,
        FailureCategory.USER_INPUT_REQUIRED,
    ):
        decision = decide_recovery(category, 1)
        assert decision.should_retry is False
        assert decision.next_state == "waiting_for_user"
