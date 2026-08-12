from enum import StrEnum


class ApplicationEmailOutcome(StrEnum):
    REJECTED = "rejected"
    NEXT_STAGE = "next_stage"
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
    EmailCategory.OFFER: ApplicationEmailOutcome.NEXT_STAGE,
    EmailCategory.TEST_ASSIGNMENT: ApplicationEmailOutcome.NEXT_STAGE,
}


def outcome_for_category(category: EmailCategory) -> ApplicationEmailOutcome:
    return _CATEGORY_OUTCOME_MAP.get(category, ApplicationEmailOutcome.UNKNOWN)
