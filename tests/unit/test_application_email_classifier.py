import pytest

from app.domain.application_email import ApplicationEmailOutcome, EmailCategory
from app.services.application_email_classifier import (
    classify_application_email,
    classify_email_category,
)


@pytest.mark.parametrize(
    ("subject", "body"),
    [
        ("Application update", "Unfortunately, we will not be moving forward."),
        ("Ответ по вакансии", "К сожалению, мы не готовы продолжить общение."),
    ],
)
def test_application_email_classifier_detects_rejection(subject: str, body: str) -> None:
    assert classify_application_email(subject, body) is ApplicationEmailOutcome.REJECTED


@pytest.mark.parametrize(
    ("subject", "body"),
    [
        ("Next stage", "Please schedule an interview with our team."),
        ("Следующий этап", "Приглашаем вас на собеседование."),
    ],
)
def test_application_email_classifier_detects_next_stage(subject: str, body: str) -> None:
    assert classify_application_email(subject, body) is ApplicationEmailOutcome.NEXT_STAGE


def test_application_email_classifier_rejects_ambiguous_or_unrelated_messages() -> None:
    assert (
        classify_application_email(
            "Next stage update",
            "Unfortunately, we will not be moving forward.",
        )
        is ApplicationEmailOutcome.UNKNOWN
    )
    assert (
        classify_application_email("Newsletter", "Read this week's hiring news.")
        is ApplicationEmailOutcome.UNKNOWN
    )


@pytest.mark.parametrize(
    ("subject", "body", "expected"),
    [
        (
            "Application update",
            "Unfortunately, we will not be moving forward.",
            EmailCategory.REJECTION,
        ),
        (
            "Interview invitation",
            "We would like to invite you to an interview.",
            EmailCategory.INTERVIEW_INVITATION,
        ),
        (
            "Reschedule",
            "We need to reschedule the interview.",
            EmailCategory.INTERVIEW_RESCHEDULE,
        ),
        (
            "Coding challenge",
            "Please complete this coding assignment.",
            EmailCategory.TEST_ASSIGNMENT,
        ),
        (
            "Offer letter",
            "We are pleased to extend a compensation package.",
            EmailCategory.OFFER,
        ),
        (
            "Application received",
            "Thank you for applying. Your application has been received.",
            EmailCategory.APPLICATION_RECEIVED,
        ),
        (
            "Sourcing",
            "Our recruiting team is interested in your profile.",
            EmailCategory.RECRUITER_CONTACT,
        ),
        (
            "Follow-up",
            "Could you clarify your availability?",
            EmailCategory.QUESTION,
        ),
        (
            "Newsletter",
            "Read this week's hiring news.",
            EmailCategory.OTHER,
        ),
    ],
)
def test_classify_email_category(
    subject: str, body: str, expected: EmailCategory
) -> None:
    assert classify_email_category(subject, body) is expected


def test_category_outcome_mapping() -> None:
    from app.domain.application_email import outcome_for_category

    assert outcome_for_category(EmailCategory.REJECTION) is ApplicationEmailOutcome.REJECTED
    assert (
        outcome_for_category(EmailCategory.INTERVIEW_INVITATION)
        is ApplicationEmailOutcome.NEXT_STAGE
    )
    assert outcome_for_category(EmailCategory.OTHER) is ApplicationEmailOutcome.UNKNOWN
