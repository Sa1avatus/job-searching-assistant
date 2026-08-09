import pytest

from app.domain.application_email import ApplicationEmailOutcome
from app.services.application_email_classifier import classify_application_email


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
