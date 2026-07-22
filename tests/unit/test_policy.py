from app.domain.models import ApplicationQuestion, ProfileFact, SubmissionMode, Vacancy
from app.domain.policy import assess_vacancy, can_submit, prepare_answers


def test_assessment_uses_only_verified_skills() -> None:
    vacancy = Vacancy(
        source_url="https://example.test/jobs/1",
        title="Engineer",
        company="Example",
        required_skills=frozenset({"Python", "Kubernetes"}),
        preferred_skills=frozenset({"PostgreSQL"}),
    )
    facts = [
        ProfileFact("skill", "Python", "advanced"),
        ProfileFact("skill", "Kubernetes", "learning", is_verified=False),
        ProfileFact("skill", "PostgreSQL", "production"),
    ]

    assessment = assess_vacancy(vacancy, facts)

    assert assessment.score == 65
    assert assessment.missing_required_skills == ("kubernetes",)
    assert assessment.recommendation == "review"


def test_missing_required_answer_blocks_automatic_submission() -> None:
    questions = [ApplicationQuestion("email", "Email", "email")]

    answers = prepare_answers(questions, [])

    assert answers[0].answer is None
    assert answers[0].requires_review is True
    assert can_submit(SubmissionMode.AUTOMATIC, answers, source_is_authorized=True) is False


def test_sensitive_answer_always_requires_review() -> None:
    questions = [ApplicationQuestion("auth", "Authorized?", "work_authorization")]
    facts = [ProfileFact("legal", "work_authorization", "Yes")]

    answers = prepare_answers(questions, facts)

    assert answers[0].answer == "Yes"
    assert answers[0].requires_review is True
    assert can_submit(SubmissionMode.AUTOMATIC, answers, source_is_authorized=True) is False


def test_complete_nonsensitive_answers_can_be_automatically_submitted() -> None:
    questions = [ApplicationQuestion("email", "Email", "email")]
    facts = [ProfileFact("contact", "email", "candidate@example.test")]

    answers = prepare_answers(questions, facts)

    assert can_submit(SubmissionMode.REVIEW, answers, source_is_authorized=True) is False
    assert can_submit(SubmissionMode.AUTOMATIC, answers, source_is_authorized=False) is False
    assert can_submit(SubmissionMode.AUTOMATIC, answers, source_is_authorized=True) is True
