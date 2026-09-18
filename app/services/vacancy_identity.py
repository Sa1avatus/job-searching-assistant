"""Look up an already stored vacancy by its canonical identity instead of its raw URL."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.vacancy_identity import canonicalize_vacancy_url
from app.storage.tables import VacancyRow


def find_vacancy_by_identity(
    session: Session, source_url: str, adapter_name: str = "generic"
) -> VacancyRow | None:
    """Return the oldest stored vacancy that is the same posting as ``source_url``.

    Matching order: (source_key, source_id) when the source exposes a posting id,
    then the canonical URL, then the exact stored URL. The oldest row wins so the
    result is deterministic even if historical duplicates exist.
    """
    identity = canonicalize_vacancy_url(source_url, adapter_name)
    oldest_first = (VacancyRow.created_at, VacancyRow.id)
    if identity.source_id is not None:
        found = session.scalar(
            select(VacancyRow)
            .where(
                VacancyRow.source_key == identity.source_key,
                VacancyRow.source_id == identity.source_id,
            )
            .order_by(*oldest_first)
            .limit(1)
        )
        if found is not None:
            return found
    found = session.scalar(
        select(VacancyRow)
        .where(VacancyRow.canonical_url == identity.canonical_url)
        .order_by(*oldest_first)
        .limit(1)
    )
    if found is not None:
        return found
    return session.scalar(select(VacancyRow).where(VacancyRow.source_url == source_url).limit(1))
