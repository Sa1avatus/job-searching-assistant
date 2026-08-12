"""Validate email category and outcome contract completeness.

These tests verify that all EmailCategory values have explicit outcome mappings
and that the classifier covers all categories with at least one pattern.
"""

from __future__ import annotations

from app.domain.application_email import (
    APPLICATION_EMAIL_CATEGORIES,
    ApplicationEmailOutcome,
    EmailCategory,
    outcome_for_category,
)
from app.services.application_email_classifier import (
    _CATEGORY_PATTERNS,
    classify_application_email,
    classify_email_category,
)


def test_all_email_categories_have_explicit_definitions() -> None:
    assert len(APPLICATION_EMAIL_CATEGORIES) == 10
    assert "rejection" in APPLICATION_EMAIL_CATEGORIES
    assert "offer" in APPLICATION_EMAIL_CATEGORIES
    assert "other" in APPLICATION_EMAIL_CATEGORIES


def test_all_categories_have_outcome_mapping() -> None:
    for category in EmailCategory:
        outcome = outcome_for_category(category)
        assert isinstance(outcome, ApplicationEmailOutcome)


def test_rejection_maps_to_rejected_outcome() -> None:
    assert outcome_for_category(EmailCategory.REJECTION) is ApplicationEmailOutcome.REJECTED


def test_offer_maps_to_offer_outcome() -> None:
    assert outcome_for_category(EmailCategory.OFFER) is ApplicationEmailOutcome.OFFER


def test_interview_maps_to_next_stage_outcome() -> None:
    assert (
        outcome_for_category(EmailCategory.INTERVIEW_INVITATION)
        is ApplicationEmailOutcome.NEXT_STAGE
    )


def test_other_maps_to_unknown_outcome() -> None:
    assert outcome_for_category(EmailCategory.OTHER) is ApplicationEmailOutcome.UNKNOWN


def test_all_pattern_categories_exist_in_enum() -> None:
    for category in _CATEGORY_PATTERNS:
        assert isinstance(category, EmailCategory)


def test_classifier_covers_all_pattern_categories() -> None:
    for category, patterns in _CATEGORY_PATTERNS.items():
        assert len(patterns) > 0, f"{category.value} has no patterns"


def test_classify_returns_valid_category() -> None:
    result = classify_email_category("Test", "Some body text")
    assert isinstance(result, EmailCategory)


def test_classify_returns_valid_outcome() -> None:
    result = classify_application_email("Test", "Some body text")
    assert isinstance(result, ApplicationEmailOutcome)
