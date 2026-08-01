from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.autofill_keys import validate_autofill_key
from app.security.autofill_encryption import encrypt_autofill_value
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import AutofillValueRow, UserRow


def update_autofill_value(
    session: Session,
    *,
    user_id: str,
    key: str,
    serialized_value: str,
    encryption_key: str,
) -> AutofillValueRow:
    if session.get(UserRow, user_id) is None:
        raise EntityNotFoundError("User not found")

    validated_key = validate_autofill_key(key)
    row = session.scalar(
        select(AutofillValueRow).where(
            AutofillValueRow.user_id == user_id,
            AutofillValueRow.key == validated_key,
        )
    )
    if row is None:
        raise EntityNotFoundError("Autofill value not found")
    if not isinstance(serialized_value, str):
        raise TypeError("serialized_value must be a string")

    row.encrypted_value = encrypt_autofill_value(serialized_value, encryption_key=encryption_key)
    session.commit()
    session.refresh(row)
    return row
