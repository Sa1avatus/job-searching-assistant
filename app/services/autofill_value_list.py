from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.autofill_sensitivity import evaluate_autofill_usage
from app.security.autofill_decryption import decrypt_autofill_value
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import AutofillValueRow, UserRow


@dataclass(frozen=True, slots=True)
class AutofillValueView:
    id: str
    user_id: str
    key: str
    label: str
    value_type: str
    serialized_value: str
    is_sensitive: bool
    requires_review: bool
    may_send_to_llm: bool
    created_at: datetime
    updated_at: datetime


def list_autofill_values(
    session: Session, *, user_id: str, encryption_key: str
) -> list[AutofillValueView]:
    if session.get(UserRow, user_id) is None:
        raise EntityNotFoundError("User not found")

    rows = session.scalars(
        select(AutofillValueRow)
        .where(AutofillValueRow.user_id == user_id)
        .order_by(AutofillValueRow.key)
    ).all()
    return [
        AutofillValueView(
            id=row.id,
            user_id=row.user_id,
            key=row.key,
            label=row.label,
            value_type=row.value_type,
            serialized_value=decrypt_autofill_value(
                row.encrypted_value, encryption_key=encryption_key
            ),
            is_sensitive=row.is_sensitive,
            requires_review=evaluate_autofill_usage(
                is_sensitive=row.is_sensitive
            ).requires_review,
            may_send_to_llm=evaluate_autofill_usage(
                is_sensitive=row.is_sensitive
            ).may_send_to_llm,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
