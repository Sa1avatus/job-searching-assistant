from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.services.recruitment import EntityNotFoundError
from app.storage.tables import EmailIntegrationRow, UserRow


class InvalidEmailIntegration(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EmailIntegration:
    user_id: str
    host: str
    port: int
    username: str
    password: str
    use_ssl: bool
    mailbox: str
    enabled: bool


class EmailIntegrationService:
    def __init__(self, session: Session, *, encryption_key: str) -> None:
        self._session = session
        try:
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise InvalidEmailIntegration("Email credential encryption key is invalid") from error

    def save(
        self,
        *,
        user_id: str,
        host: str,
        port: int,
        username: str,
        password: str | None,
        use_ssl: bool,
        mailbox: str,
        enabled: bool,
    ) -> EmailIntegrationRow:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")
        normalized_host = host.strip().casefold()
        normalized_username = username.strip()
        normalized_mailbox = mailbox.strip()
        if not normalized_host or len(normalized_host) > 255 or "://" in normalized_host:
            raise InvalidEmailIntegration("IMAP host is invalid")
        if port < 1 or port > 65535:
            raise InvalidEmailIntegration("IMAP port is invalid")
        if not normalized_username or len(normalized_username) > 320:
            raise InvalidEmailIntegration("IMAP username is invalid")
        if not normalized_mailbox or len(normalized_mailbox) > 255:
            raise InvalidEmailIntegration("IMAP mailbox is invalid")

        row = self._session.get(EmailIntegrationRow, user_id)
        normalized_password = (password or "").strip()
        if not normalized_password and row is None:
            raise InvalidEmailIntegration("IMAP password is required")
        if normalized_password:
            encrypted_password = self._cipher.encrypt(normalized_password.encode()).decode("ascii")
        else:
            assert row is not None
            encrypted_password = row.encrypted_password

        now = datetime.now(UTC)
        row = row or EmailIntegrationRow(user_id=user_id, created_at=now)
        row.host = normalized_host
        row.port = port
        row.username = normalized_username
        row.encrypted_password = encrypted_password
        row.use_ssl = use_ssl
        row.mailbox = normalized_mailbox
        row.enabled = enabled
        row.updated_at = now
        self._session.add(row)
        self._session.commit()
        return row

    def load(self, user_id: str) -> EmailIntegration | None:
        row = self._session.get(EmailIntegrationRow, user_id)
        if row is None:
            return None
        try:
            password = self._cipher.decrypt(row.encrypted_password.encode("ascii")).decode()
        except (InvalidToken, ValueError, UnicodeError) as error:
            raise InvalidEmailIntegration("Saved email credential cannot be decrypted") from error
        return EmailIntegration(
            user_id=row.user_id,
            host=row.host,
            port=row.port,
            username=row.username,
            password=password,
            use_ssl=row.use_ssl,
            mailbox=row.mailbox,
            enabled=row.enabled,
        )

    def get_row(self, user_id: str) -> EmailIntegrationRow | None:
        return self._session.get(EmailIntegrationRow, user_id)
