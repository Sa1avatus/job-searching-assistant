"""Lifecycle of a user-defined site's search recipe: draft, verify by a real run, then activate.

A draft never runs during discovery. It becomes eligible only after a verification run returned
results, and activation replaces the previous active version without deleting it, so a broken
recipe can be rolled back by activating an older version again.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.search_recipe import SearchRecipe, validate_recipe
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import SiteDefinitionRow, SiteSearchRecipeRow


class RecipeNotVerified(ValueError):
    """Activation was requested for a recipe that has not produced results yet."""


def login_path_markers_for(site: SiteDefinitionRow) -> tuple[str, ...]:
    """Path fragments that mean "this is the sign-in page, not a result" for this site."""
    raw_markers = site.authorization_rules.get("login_path_markers", [])
    if not isinstance(raw_markers, list):
        raw_markers = []
    markers = tuple(
        marker
        for marker in raw_markers
        if isinstance(marker, str) and marker.startswith("/") and len(marker) <= 500
    )
    if markers:
        return markers
    return (urlsplit(site.login_url).path or "/",)


def site_payload(site: SiteDefinitionRow) -> dict[str, object]:
    return {
        "site_key": site.site_key,
        "allowed_hosts": list(site.allowed_hosts),
        "login_path_markers": list(login_path_markers_for(site)),
    }


def get_site(session: Session, user_id: str, site_definition_id: str) -> SiteDefinitionRow:
    site = session.scalar(
        select(SiteDefinitionRow).where(
            SiteDefinitionRow.id == site_definition_id,
            SiteDefinitionRow.user_id == user_id,
            SiteDefinitionRow.archived_at.is_(None),
        )
    )
    if site is None:
        raise EntityNotFoundError("Site definition not found")
    return site


def list_recipes(session: Session, site: SiteDefinitionRow) -> list[SiteSearchRecipeRow]:
    return list(
        session.scalars(
            select(SiteSearchRecipeRow)
            .where(SiteSearchRecipeRow.site_definition_id == site.id)
            .order_by(SiteSearchRecipeRow.version.desc())
        )
    )


def get_recipe(session: Session, site: SiteDefinitionRow, version: int) -> SiteSearchRecipeRow:
    row = session.scalar(
        select(SiteSearchRecipeRow).where(
            SiteSearchRecipeRow.site_definition_id == site.id,
            SiteSearchRecipeRow.version == version,
        )
    )
    if row is None:
        raise EntityNotFoundError("Search recipe version not found")
    return row


def save_draft(
    session: Session,
    site: SiteDefinitionRow,
    recipe: SearchRecipe,
    *,
    learned_from_url: str | None = None,
    preview: list[dict[str, str]] | None = None,
) -> SiteSearchRecipeRow:
    validated = validate_recipe(recipe, site.allowed_hosts)
    for stale in list_recipes(session, site):
        if stale.status == "draft":
            stale.status = "archived"
    next_version = (
        session.scalar(
            select(func.max(SiteSearchRecipeRow.version)).where(
                SiteSearchRecipeRow.site_definition_id == site.id
            )
        )
        or 0
    ) + 1
    row = SiteSearchRecipeRow(
        site_definition_id=site.id,
        version=next_version,
        status="draft",
        recipe=validated.to_dict(),
        learned_from_url=learned_from_url,
        preview=preview or [],
        verified_at=datetime.now(UTC) if preview else None,
    )
    session.add(row)
    session.commit()
    return row


def record_verification(
    session: Session, row: SiteSearchRecipeRow, preview: list[dict[str, str]]
) -> SiteSearchRecipeRow:
    row.preview = preview
    row.verified_at = datetime.now(UTC) if preview else None
    session.commit()
    return row


def activate(session: Session, site: SiteDefinitionRow, version: int) -> SiteSearchRecipeRow:
    row = get_recipe(session, site, version)
    if row.verified_at is None:
        raise RecipeNotVerified(
            "Рецепт не проверен: сначала запустите пробный поиск, который вернёт результаты"
        )
    validate_recipe(SearchRecipe.from_dict(row.recipe), site.allowed_hosts)
    for current in list_recipes(session, site):
        if current.status == "active" and current.id != row.id:
            current.status = "archived"
    session.flush()  # free the one-active-recipe index before promoting the new version
    row.status = "active"
    session.commit()
    return row


def archive_recipe(session: Session, site: SiteDefinitionRow, version: int) -> SiteSearchRecipeRow:
    row = get_recipe(session, site, version)
    row.status = "archived"
    session.commit()
    return row


def active_recipe(session: Session, site: SiteDefinitionRow) -> SearchRecipe | None:
    row = session.scalar(
        select(SiteSearchRecipeRow).where(
            SiteSearchRecipeRow.site_definition_id == site.id,
            SiteSearchRecipeRow.status == "active",
        )
    )
    return SearchRecipe.from_dict(row.recipe) if row is not None else None
