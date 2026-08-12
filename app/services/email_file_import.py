from __future__ import annotations

import mailbox
from email import message_from_bytes, policy
from pathlib import Path

from app.services.application_email_sync import ApplicationEmailMessage
from app.services.imap_email_provider import _decode_header_value, _plain_text_body


class EmlImportProvider:
    """Import emails from .eml files on disk."""

    def __init__(
        self,
        paths: list[Path],
        *,
        max_files: int = 100,
    ) -> None:
        if not paths:
            raise ValueError("At least one EML path is required")
        if len(paths) > max_files:
            raise ValueError(
                f"Too many EML files: {len(paths)} exceeds limit {max_files}"
            )
        self._paths = paths

    async def fetch_messages(self) -> list[ApplicationEmailMessage]:
        messages: list[ApplicationEmailMessage] = []
        for path in self._paths:
            try:
                raw = path.read_bytes()
                message = message_from_bytes(raw, policy=policy.default)
                messages.append(
                    ApplicationEmailMessage(
                        subject=_decode_header_value(message.get("Subject", "")),
                        body=_plain_text_body(message),
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        return messages


class MboxImportProvider:
    """Import emails from an mbox file."""

    def __init__(
        self,
        path: Path,
        *,
        max_messages: int = 500,
    ) -> None:
        self._path = path
        self._max_messages = max_messages

    async def fetch_messages(self) -> list[ApplicationEmailMessage]:
        import mailbox

        messages: list[ApplicationEmailMessage] = []
        try:
            mbox = mailbox.mbox(str(self._path))
            for i, message in enumerate(mbox):
                if i >= self._max_messages:
                    break
                try:
                    subject = _decode_header_value(
                        str(message.get("Subject", ""))
                    )
                    body = _extract_mbox_body(message)
                    messages.append(
                        ApplicationEmailMessage(subject=subject, body=body)
                    )
                except Exception:  # noqa: BLE001
                    continue
            mbox.close()
        except Exception:  # noqa: BLE001
            pass
        return messages


def _extract_mbox_body(message: mailbox.mboxMessage) -> str:
    if message.is_multipart():
        parts: list[str] = []
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    parts.append(payload.decode(charset, errors="replace"))
        return "\n".join(parts)
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = message.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    return ""
