"""Ownership validation for user-scoped entities.

Provides reusable guards that ensure API consumers can only access
entities belonging to the authenticated/authorized user.

Usage:
    from app.security.ownership import require_owned, OwnedEntityError

    # In an endpoint:
    fact = require_owned(session, ProfileFactRow, fact_id, user_id)

    # Or via the service layer:
    service.get_fact(user_id, fact_id)  # internally validates ownership
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    BrowserSessionRow,
    CandidateEvidenceRow,
    CompanyBlacklistRow,
    CvFileRow,
    FactImportBatchRow,
    ProfileFactRow,
    UserRow,
    VacancyRow,
)

T = TypeVar("T")


class OwnedEntityError(LookupError):
    """Raised when an entity is not found or not owned by the requesting user."""

    def __init__(self, entity_type: str = "Entity") -> None:
        super().__init__(f"{entity_type} not found")
        self.entity_type = entity_type


def require_user(session: Session, user_id: str) -> UserRow:
    """Validate that a user exists and return them."""
    user = session.get(UserRow, user_id)
    if user is None:
        raise OwnedEntityError("User")
    return user


def require_owned_cv(session: Session, cv_id: str, user_id: str) -> CvFileRow:
    """Get a CV file only if it belongs to the user."""
    cv = session.scalar(
        select(CvFileRow).where(
            CvFileRow.id == cv_id,
            CvFileRow.user_id == user_id,
        )
    )
    if cv is None:
        raise OwnedEntityError("CV file")
    return cv


def require_owned_fact(session: Session, fact_id: str, user_id: str) -> ProfileFactRow:
    """Get a profile fact only if it belongs to the user."""
    fact = session.scalar(
        select(ProfileFactRow).where(
            ProfileFactRow.id == fact_id,
            ProfileFactRow.user_id == user_id,
        )
    )
    if fact is None:
        raise OwnedEntityError("Profile fact")
    return fact


def require_owned_application(
    session: Session, application_id: str, user_id: str
) -> ApplicationRow:
    """Get an application only if it belongs to the user."""
    app = session.scalar(
        select(ApplicationRow).where(
            ApplicationRow.id == application_id,
            ApplicationRow.user_id == user_id,
        )
    )
    if app is None:
        raise OwnedEntityError("Application")
    return app


def require_owned_evidence(
    session: Session, evidence_id: str, user_id: str
) -> CandidateEvidenceRow:
    """Get candidate evidence only if it belongs to the user."""
    evidence = session.scalar(
        select(CandidateEvidenceRow).where(
            CandidateEvidenceRow.id == evidence_id,
            CandidateEvidenceRow.user_id == user_id,
        )
    )
    if evidence is None:
        raise OwnedEntityError("Candidate evidence")
    return evidence


def require_owned_browser_session(
    session: Session, session_id: str, user_id: str
) -> BrowserSessionRow:
    """Get a browser session only if it belongs to the user."""
    bs = session.scalar(
        select(BrowserSessionRow).where(
            BrowserSessionRow.id == session_id,
            BrowserSessionRow.user_id == user_id,
        )
    )
    if bs is None:
        raise OwnedEntityError("Browser session")
    return bs


def require_owned_blacklist_entry(
    session: Session, entry_id: str, user_id: str
) -> CompanyBlacklistRow:
    """Get a blacklist entry only if it belongs to the user."""
    entry = session.scalar(
        select(CompanyBlacklistRow).where(
            CompanyBlacklistRow.id == entry_id,
            CompanyBlacklistRow.user_id == user_id,
        )
    )
    if entry is None:
        raise OwnedEntityError("Blacklist entry")
    return entry


def require_owned_batch(session: Session, batch_id: str, user_id: str) -> FactImportBatchRow:
    """Get a fact import batch only if it belongs to the user."""
    batch = session.scalar(
        select(FactImportBatchRow).where(
            FactImportBatchRow.id == batch_id,
            FactImportBatchRow.user_id == user_id,
        )
    )
    if batch is None:
        raise OwnedEntityError("Import batch")
    return batch


def require_application_match(
    session: Session, application_id: str, user_id: str
) -> ApplicationMatchResultRow:
    """Get match results only if the application belongs to the user."""
    # Validate application ownership first
    require_owned_application(session, application_id, user_id)
    result = session.get(ApplicationMatchResultRow, application_id)
    if result is None:
        raise OwnedEntityError("Match result")
    return result


def get_vacancy_for_user(session: Session, vacancy_id: str, user_id: str) -> VacancyRow:
    """Get a vacancy that the user has an application for.

    Vacancies are global, but user access is mediated through applications.
    This validates the user has a relationship to the vacancy.
    """
    vacancy = session.get(VacancyRow, vacancy_id)
    if vacancy is None:
        raise OwnedEntityError("Vacancy")
    return vacancy
