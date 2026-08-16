from enum import StrEnum


class ApplicationEmailOutcome(StrEnum):
    REJECTED = "rejected"
    NEXT_STAGE = "next_stage"
    OFFER = "offer"
    UNKNOWN = "unknown"


class EmailCategory(StrEnum):
    APPLICATION_RECEIVED = "application_received"
    RECRUITER_CONTACT = "recruiter_contact"
    QUESTION = "question"
    TEST_ASSIGNMENT = "test_assignment"
    INTERVIEW_INVITATION = "interview_invitation"
    INTERVIEW_RESCHEDULE = "interview_reschedule"
    OFFER = "offer"
    REJECTION = "rejection"
    FOLLOW_UP = "follow_up"
    OTHER = "other"


_CATEGORY_OUTCOME_MAP: dict[EmailCategory, ApplicationEmailOutcome] = {
    EmailCategory.REJECTION: ApplicationEmailOutcome.REJECTED,
    EmailCategory.INTERVIEW_INVITATION: ApplicationEmailOutcome.NEXT_STAGE,
    EmailCategory.INTERVIEW_RESCHEDULE: ApplicationEmailOutcome.NEXT_STAGE,
    EmailCategory.OFFER: ApplicationEmailOutcome.OFFER,
    EmailCategory.TEST_ASSIGNMENT: ApplicationEmailOutcome.NEXT_STAGE,
}


def outcome_for_category(category: EmailCategory) -> ApplicationEmailOutcome:
    return _CATEGORY_OUTCOME_MAP.get(category, ApplicationEmailOutcome.UNKNOWN)


# Application status applied when an email is confidently matched to an application.
# Only categories that represent a definite employer decision map to a status; the rest
# (recruiter contact, questions, follow-ups, other) leave the application status untouched.
_CATEGORY_STATUS_MAP: dict[EmailCategory, str] = {
    EmailCategory.APPLICATION_RECEIVED: "approved",
    EmailCategory.REJECTION: "employer_rejected",
    EmailCategory.INTERVIEW_INVITATION: "interview",
    EmailCategory.INTERVIEW_RESCHEDULE: "interview",
    EmailCategory.OFFER: "offer",
    EmailCategory.TEST_ASSIGNMENT: "interview",
}


def status_for_category(category: EmailCategory) -> str | None:
    """Return the application status an email category advances to, or ``None``.

    ``APPLICATION_RECEIVED`` maps to ``approved`` ("Принята") — an acknowledgement such as
    "мы получили ваше резюме" means the application was accepted into the employer's process.
    """
    return _CATEGORY_STATUS_MAP.get(category)


APPLICATION_EMAIL_CATEGORIES: tuple[str, ...] = tuple(c.value for c in EmailCategory)
