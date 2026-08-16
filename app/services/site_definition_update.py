from copy import deepcopy
from math import isfinite

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.site_access import validate_site_access
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import SiteDefinitionRow, UserRow


class InvalidSiteDefinitionUpdate(ValueError):
    """Raised when a site-definition update is invalid."""


def update_site_definition(
    session: Session,
    *,
    user_id: str,
    site_definition_id: str,
    name: str,
    login_url: str,
    allowed_hosts: list[str],
    authorization_rules: dict[str, object] | None = None,
) -> SiteDefinitionRow:
    """Update mutable fields of one active, user-owned site definition."""

    def invalid_rules() -> InvalidSiteDefinitionUpdate:
        return InvalidSiteDefinitionUpdate("Authorization rules are invalid")

    value_count = 0

    def validate_json_value(value: object, *, depth: int) -> None:
        nonlocal value_count
        value_count += 1
        if value_count > 200 or depth > 5:
            raise invalid_rules()
        if value is None or type(value) in {bool, int, str}:
            return
        if type(value) is float:
            if not isfinite(value):
                raise invalid_rules()
            return
        if type(value) is list:
            for item in value:
                validate_json_value(item, depth=depth + 1)
            return
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise invalid_rules()
                validate_json_value(item, depth=depth + 1)
            return
        raise invalid_rules()

    if session.get(UserRow, user_id) is None:
        raise EntityNotFoundError("User not found")
    row = session.scalar(
        select(SiteDefinitionRow).where(
            SiteDefinitionRow.id == site_definition_id,
            SiteDefinitionRow.user_id == user_id,
        )
    )
    if row is None or row.archived_at is not None:
        raise EntityNotFoundError("Site definition not found")
    if type(name) is not str:
        raise InvalidSiteDefinitionUpdate("Site name is invalid")
    normalized_name = " ".join(name.split())
    if not 1 <= len(normalized_name) <= 200:
        raise InvalidSiteDefinitionUpdate("Site name is invalid")
    if authorization_rules is not None and type(authorization_rules) is not dict:
        raise invalid_rules()

    rules = authorization_rules or {}
    validate_json_value(rules, depth=0)
    validated_access = validate_site_access(
        login_url=login_url,
        allowed_hosts=allowed_hosts,
    )

    row.name = normalized_name
    row.login_url = validated_access.login_url
    row.allowed_hosts = list(validated_access.allowed_hosts)
    row.authorization_rules = deepcopy(rules)
    session.commit()
    return row
