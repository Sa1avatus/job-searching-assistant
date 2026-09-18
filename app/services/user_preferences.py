"""Load, validate and persist a user's search preferences; evaluate them against a vacancy."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.preferences import (
    ConstraintViolation,
    UserPreferences,
    VacancyFacts,
    evaluate_constraints,
    validate_preferences,
)
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import UserPreferenceRow, UserRow, VacancyRow


class UserPreferencesService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str) -> UserPreferences:
        """Stored preferences, or the empty defaults when the user never saved any."""
        self._require_user(user_id)
        row = self._session.get(UserPreferenceRow, user_id)
        if row is None:
            return UserPreferences()
        return UserPreferences(
            min_salary=row.min_salary,
            salary_currency=row.salary_currency,
            preferred_locations=tuple(row.preferred_locations or ()),
            work_formats=tuple(row.work_formats or ()),
            employment_types=tuple(row.employment_types or ()),
        )

    def save(
        self,
        user_id: str,
        *,
        min_salary: int | None,
        salary_currency: str,
        preferred_locations: list[str],
        work_formats: list[str],
        employment_types: list[str],
    ) -> UserPreferences:
        self._require_user(user_id)
        preferences = validate_preferences(
            min_salary=min_salary,
            salary_currency=salary_currency,
            preferred_locations=preferred_locations,
            work_formats=work_formats,
            employment_types=employment_types,
        )
        row = self._session.get(UserPreferenceRow, user_id)
        if row is None:
            row = UserPreferenceRow(user_id=user_id)
            self._session.add(row)
        row.min_salary = preferences.min_salary
        row.salary_currency = preferences.salary_currency
        row.preferred_locations = list(preferences.preferred_locations)
        row.work_formats = list(preferences.work_formats)
        row.employment_types = list(preferences.employment_types)
        self._session.commit()
        return preferences

    def violations_for(self, user_id: str, vacancy: VacancyRow) -> list[ConstraintViolation]:
        return evaluate_constraints(
            self.get(user_id),
            VacancyFacts(
                location=vacancy.location or "",
                work_format=vacancy.work_format or "unspecified",
                employment_types=tuple(vacancy.employment_types or ()),
                salary_text=vacancy.salary_text or "",
            ),
        )

    def _require_user(self, user_id: str) -> None:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")
