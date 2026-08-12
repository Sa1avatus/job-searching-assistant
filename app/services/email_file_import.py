from __future__ import annotations

import mailbox
import zipfile
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


class ZipEmlImportProvider:
    """Import .eml files from a ZIP archive with path traversal protection."""

    def __init__(
        self,
        zip_path: Path,
        *,
        max_files: int = 100,
        max_uncompressed_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self._zip_path = zip_path
        self._max_files = max_files
        self._max_uncompressed = max_uncompressed_bytes

    async def fetch_messages(self) -> list[ApplicationEmailMessage]:
        messages: list[ApplicationEmailMessage] = []
        try:
            with zipfile.ZipFile(self._zip_path) as zf:
                total_size = sum(info.file_size for info in zf.infolist())
                if total_size > self._max_uncompressed:
                    return []
                eml_count = 0
                for info in zf.infolist():
                    if eml_count >= self._max_files:
                        break
                    if not info.filename.endswith(".eml"):
                        continue
                    if _is_unsafe_path(info.filename):
                        continue
                    try:
                        raw = zf.read(info.filename)
                        message = message_from_bytes(raw, policy=policy.default)
                        messages.append(
                            ApplicationEmailMessage(
                                subject=_decode_header_value(
                                    message.get("Subject", "")
                                ),
                                body=_plain_text_body(message),
                            )
                        )
                        eml_count += 1
                    except Exception:  # noqa: BLE001
                        continue
        except (zipfile.BadZipFile, OSError):
            pass
        return messages


def _is_unsafe_path(filename: str) -> bool:
    parts = filename.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        return True
    return bool(filename.startswith("/"))
