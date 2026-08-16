from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.autofill import AutofillValueType
from app.domain.autofill_keys import validate_autofill_key
from app.security.autofill_encryption import encrypt_autofill_value
from app.services.recruitment import DuplicateEntityError, EntityNotFoundError
from app.storage.tables import AutofillValueRow, UserRow


class InvalidAutofillValue(ValueError):
    pass


def create_autofill_value(
    session: Session,
    *,
    user_id: str,
    key: str,
    label: str,
    value_type: AutofillValueType,
    serialized_value: str,
    is_sensitive: bool,
    encryption_key: str,
) -> AutofillValueRow:
    if session.get(UserRow, user_id) is None:
        raise EntityNotFoundError("User not found")
    validated_key = validate_autofill_key(key)
    if not isinstance(label, str):
        raise InvalidAutofillValue("Autofill label is invalid")
    normalized_label = " ".join(label.split())
    if not 1 <= len(normalized_label) <= 200:
        raise InvalidAutofillValue("Autofill label is invalid")
    if not isinstance(value_type, AutofillValueType):
        raise InvalidAutofillValue("Autofill value type is invalid")
    if not isinstance(is_sensitive, bool):
        raise InvalidAutofillValue("Autofill sensitivity flag is invalid")
    existing = session.scalar(
        select(AutofillValueRow).where(
            AutofillValueRow.user_id == user_id,
            AutofillValueRow.key == validated_key,
        )
    )
    if existing is not None:
        raise DuplicateEntityError("Autofill key already exists")
    encrypted_value = encrypt_autofill_value(
        serialized_value,
        encryption_key=encryption_key,
    )
    row = AutofillValueRow(
        user_id=user_id,
        key=validated_key,
        label=normalized_label,
        value_type=value_type.value,
        encrypted_value=encrypted_value,
        is_sensitive=is_sensitive,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise DuplicateEntityError("Autofill key already exists") from error
    return row
