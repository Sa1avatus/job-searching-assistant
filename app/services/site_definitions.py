from copy import deepcopy
from math import isfinite

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.site_access import validate_site_access
from app.services.recruitment import DuplicateEntityError, EntityNotFoundError
from app.storage.tables import SiteDefinitionRow, UserRow


class InvalidSiteDefinition(ValueError):
    """Raised when a site definition cannot be stored safely."""


def create_site_definition(
    session: Session,
    *,
    user_id: str,
    site_key: str,
    name: str,
    login_url: str,
    allowed_hosts: list[str],
    authorization_rules: dict[str, object] | None = None,
) -> SiteDefinitionRow:
    """Validate and persist one user-owned site definition."""

    def invalid_rules() -> InvalidSiteDefinition:
        return InvalidSiteDefinition("Authorization rules are invalid")

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
    if type(site_key) is not str:
        raise InvalidSiteDefinition("Site key is invalid")
    normalized_site_key = site_key.strip().casefold()
    if not 1 <= len(normalized_site_key) <= 100 or not all(
        character in "abcdefghijklmnopqrstuvwxyz0123456789._-"
        for character in normalized_site_key
    ):
        raise InvalidSiteDefinition("Site key is invalid")
    if type(name) is not str:
        raise InvalidSiteDefinition("Site name is invalid")
    normalized_name = " ".join(name.split())
    if not 1 <= len(normalized_name) <= 200:
        raise InvalidSiteDefinition("Site name is invalid")
    if authorization_rules is not None and type(authorization_rules) is not dict:
        raise invalid_rules()

    rules = authorization_rules or {}
    validate_json_value(rules, depth=0)
    validated_access = validate_site_access(
        login_url=login_url,
        allowed_hosts=allowed_hosts,
    )
    existing = session.scalar(
        select(SiteDefinitionRow).where(
            SiteDefinitionRow.user_id == user_id,
            SiteDefinitionRow.site_key == normalized_site_key,
        )
    )
    if existing is not None:
        raise DuplicateEntityError("Site key already exists")

    row = SiteDefinitionRow(
        user_id=user_id,
        site_key=normalized_site_key,
        name=normalized_name,
        login_url=validated_access.login_url,
        allowed_hosts=list(validated_access.allowed_hosts),
        authorization_rules=deepcopy(rules),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise DuplicateEntityError("Site key already exists") from error
    return row
