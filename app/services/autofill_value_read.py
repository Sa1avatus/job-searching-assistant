from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.autofill_keys import validate_autofill_key
from app.security.autofill_decryption import decrypt_autofill_value
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import AutofillValueRow, UserRow


def read_autofill_value(session: Session, *, user_id: str, key: str, encryption_key: str) -> str:
    user = session.get(UserRow, user_id)
    if user is None:
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

    return decrypt_autofill_value(row.encrypted_value, encryption_key=encryption_key)
