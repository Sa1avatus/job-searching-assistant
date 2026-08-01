from __future__ import annotations

import pytest

from app.domain.autofill_sensitivity import (
    AutofillUsagePolicy,
    evaluate_autofill_usage,
)


@pytest.mark.parametrize(
    ("is_sensitive", "expected"),
    [
        (True, AutofillUsagePolicy(requires_review=True, may_send_to_llm=False)),
        (False, AutofillUsagePolicy(requires_review=False, may_send_to_llm=True)),
    ],
)
def test_evaluate_autofill_usage_returns_expected_policy(
    is_sensitive: bool, expected: AutofillUsagePolicy
) -> None:
    assert evaluate_autofill_usage(is_sensitive=is_sensitive) == expected


@pytest.mark.parametrize("invalid_value", [0, 1, None, "", "true", object()])
def test_evaluate_autofill_usage_rejects_non_boolean_values(
    invalid_value: object,
) -> None:
    with pytest.raises(TypeError, match="is_sensitive must be a boolean"):
        evaluate_autofill_usage(is_sensitive=invalid_value)  # type: ignore[arg-type]
