from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.recruitment import DuplicateEntityError, EntityNotFoundError
from app.storage.tables import CompanyBlacklistRow, UserRow


def normalize_company_name(company: str) -> str:
    return " ".join(company.split()).casefold()


class CompanyBlacklistService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_entries(self, user_id: str) -> list[CompanyBlacklistRow]:
        self._require_user(user_id)
        return list(
            self._session.scalars(
                select(CompanyBlacklistRow)
                .where(CompanyBlacklistRow.user_id == user_id)
                .order_by(func.lower(CompanyBlacklistRow.company), CompanyBlacklistRow.id)
            )
        )

    def add(self, user_id: str, company: str) -> CompanyBlacklistRow:
        self._require_user(user_id)
        normalized_company = normalize_company_name(company)
        if not normalized_company:
            raise ValueError("Company name is required")
        existing = self._session.scalar(
            select(CompanyBlacklistRow).where(
                CompanyBlacklistRow.user_id == user_id,
                CompanyBlacklistRow.normalized_company == normalized_company,
            )
        )
        if existing is not None:
            return existing
        entry = CompanyBlacklistRow(
            user_id=user_id,
            company=" ".join(company.split()),
            normalized_company=normalized_company,
        )
        self._session.add(entry)
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise DuplicateEntityError("Company is already blacklisted") from error
        return entry

    def remove(self, user_id: str, entry_id: str) -> None:
        self._require_user(user_id)
        entry = self._session.scalar(
            select(CompanyBlacklistRow).where(
                CompanyBlacklistRow.id == entry_id,
                CompanyBlacklistRow.user_id == user_id,
            )
        )
        if entry is None:
            raise EntityNotFoundError("Blacklist entry not found")
        self._session.delete(entry)
        self._session.commit()

    def contains(self, user_id: str, company: str) -> bool:
        normalized_company = normalize_company_name(company)
        if not normalized_company:
            return False
        return (
            self._session.scalar(
                select(CompanyBlacklistRow.id).where(
                    CompanyBlacklistRow.user_id == user_id,
                    CompanyBlacklistRow.normalized_company == normalized_company,
                )
            )
            is not None
        )

    def _require_user(self, user_id: str) -> None:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")
