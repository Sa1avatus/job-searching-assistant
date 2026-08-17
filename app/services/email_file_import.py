from __future__ import annotations

import io
import mailbox
import tempfile
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage, MIMEPart
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import structlog

from app.services.application_email_sync import ApplicationEmailMessage
from app.services.imap_email_provider import _decode_header_value

logger = structlog.get_logger(__name__)

_SUPPORTED_UPLOAD_SUFFIXES = frozenset({".eml", ".mbox", ".zip"})
_DEFAULT_MAX_FILES = 100
_DEFAULT_MAX_MESSAGES = 500
_DEFAULT_MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_EMAIL_IMPORT_FILES = _DEFAULT_MAX_FILES
MAX_EMAIL_IMPORT_BYTES = _DEFAULT_MAX_TOTAL_BYTES


@dataclass(frozen=True, slots=True)
class UploadedEmailFile:
    filename: str
    content: bytes


class UploadedEmailImportProvider:
    """Parse bounded user uploads without retaining their message contents."""

    def __init__(
        self,
        files: list[UploadedEmailFile],
        *,
        max_files: int = _DEFAULT_MAX_FILES,
        max_messages: int = _DEFAULT_MAX_MESSAGES,
        max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        if not files:
            raise ValueError("At least one email file is required")
        if len(files) > max_files:
            raise ValueError(f"Too many email files: {len(files)} exceeds limit {max_files}")
        if sum(len(item.content) for item in files) > max_total_bytes:
            raise ValueError("Email import exceeds the total size limit")
        for item in files:
            suffix = Path(item.filename).suffix.casefold()
            if suffix not in _SUPPORTED_UPLOAD_SUFFIXES:
                raise ValueError(f"Unsupported email file type: {suffix or 'missing extension'}")
        self._files = tuple(files)
        self._max_messages = max_messages
        self._max_total_bytes = max_total_bytes

    async def fetch_messages(self) -> list[ApplicationEmailMessage]:
        messages: list[ApplicationEmailMessage] = []
        for item in self._files:
            remaining = self._max_messages - len(messages)
            if remaining <= 0:
                break
            suffix = Path(item.filename).suffix.casefold()
            if suffix == ".eml":
                messages.extend(_parse_eml_bytes(item.content, max_messages=remaining))
            elif suffix == ".zip":
                messages.extend(
                    _parse_zip_bytes(
                        item.content,
                        max_messages=remaining,
                        max_uncompressed_bytes=self._max_total_bytes,
                    )
                )
            else:
                messages.extend(_parse_mbox_bytes(item.content, max_messages=remaining))
        return messages[: self._max_messages]


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
            raise ValueError(f"Too many EML files: {len(paths)} exceeds limit {max_files}")
        self._paths = paths

    async def fetch_messages(self) -> list[ApplicationEmailMessage]:
        messages: list[ApplicationEmailMessage] = []
        for path in self._paths:
            try:
                messages.extend(_parse_eml_bytes(path.read_bytes()))
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
        messages: list[ApplicationEmailMessage] = []
        try:
            mbox = mailbox.mbox(str(self._path))
            for message in mbox:
                if len(messages) >= self._max_messages:
                    break
                try:
                    parsed = message_from_bytes(message.as_bytes(), policy=policy.default)
                    messages.extend(
                        _parse_email_message(
                            parsed,
                            max_messages=self._max_messages - len(messages),
                        )
                    )
                except Exception as error:  # noqa: BLE001
                    err = str(error)[:200]
                    logger.warning(
                        "email_mbox_message_failed",
                        error_type=type(error).__name__,
                        error=err,
                    )
                    continue
            mbox.close()
        except Exception as error:  # noqa: BLE001
            err = str(error)[:200]
            logger.error(
                "email_mbox_parse_failed",
                error_type=type(error).__name__,
                error=err,
            )
        return messages


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
        with suppress(zipfile.BadZipFile, OSError):
            messages.extend(
                _parse_zip_bytes(
                    self._zip_path.read_bytes(),
                    max_messages=self._max_files,
                    max_uncompressed_bytes=self._max_uncompressed,
                )
            )
        return messages


def _parse_eml_bytes(
    raw: bytes, *, max_messages: int = _DEFAULT_MAX_MESSAGES
) -> list[ApplicationEmailMessage]:
    message = message_from_bytes(raw, policy=policy.default)
    return _parse_email_message(message, max_messages=max_messages)


def _parse_email_message(
    message: EmailMessage,
    *,
    max_messages: int,
) -> list[ApplicationEmailMessage]:
    if max_messages <= 0:
        return []
    messages = [
        ApplicationEmailMessage(
            subject=_decode_header_value(str(message.get("Subject", ""))),
            body=_message_text_without_nested_messages(message),
            company=_sender_company_hint(message),
        )
    ]
    if not message.is_multipart():
        return messages
    for part in message.iter_attachments():
        if len(messages) >= max_messages:
            break
        nested: list[EmailMessage] = []
        if part.get_content_type() == "message/rfc822":
            payload = part.get_payload()
            if isinstance(payload, list):
                nested.extend(item for item in payload if isinstance(item, EmailMessage))
        elif (part.get_filename() or "").casefold().endswith(".eml"):
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                nested.append(message_from_bytes(payload, policy=policy.default))
        for nested_message in nested:
            messages.extend(
                _parse_email_message(
                    nested_message,
                    max_messages=max_messages - len(messages),
                )
            )
    return messages[:max_messages]


def _message_text_without_nested_messages(message: EmailMessage | MIMEPart[Any, Any]) -> str:
    if not message.is_multipart():
        content = message.get_content()
        if not isinstance(content, str):
            return ""
        if message.get_content_type() == "text/plain":
            return content
        if message.get_content_type() == "text/html":
            return _html_to_text(content)
        return ""
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.iter_parts():
        filename = (part.get_filename() or "").casefold()
        if part.get_content_type() == "message/rfc822" or filename.endswith(".eml"):
            continue
        if part.is_multipart():
            nested_text = _message_text_without_nested_messages(part)
            if nested_text:
                plain_parts.append(nested_text)
        elif (
            part.get_content_type() == "text/plain"
            and part.get_content_disposition() != "attachment"
        ):
            content = part.get_content()
            if isinstance(content, str) and content.strip():
                plain_parts.append(content)
        elif (
            part.get_content_type() == "text/html"
            and part.get_content_disposition() != "attachment"
        ):
            content = part.get_content()
            if isinstance(content, str):
                html_text = _html_to_text(content)
                if html_text:
                    html_parts.append(html_text)
    return "\n".join(plain_parts or html_parts)


class _EmailHtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        elif tag in {"br", "p", "div", "li", "tr"} and self.parts:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag in {"p", "div", "li", "tr"} and self.parts:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    parser = _EmailHtmlTextExtractor()
    parser.feed(value)
    parser.close()
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def _sender_company_hint(message: EmailMessage) -> str | None:
    display_name, _ = parseaddr(_decode_header_value(str(message.get("From", ""))))
    normalized = " ".join(display_name.split()).strip(" -–—|,:;")
    if not normalized:
        return None
    return normalized


def _parse_zip_bytes(
    raw: bytes,
    *,
    max_messages: int,
    max_uncompressed_bytes: int,
) -> list[ApplicationEmailMessage]:
    messages: list[ApplicationEmailMessage] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        safe_eml_files = [
            info
            for info in archive.infolist()
            if info.filename.casefold().endswith(".eml") and not _is_unsafe_path(info.filename)
        ]
        if sum(info.file_size for info in safe_eml_files) > max_uncompressed_bytes:
            raise ValueError("Email archive exceeds the uncompressed size limit")
        for info in safe_eml_files:
            if len(messages) >= max_messages:
                break
            messages.extend(
                _parse_eml_bytes(
                    archive.read(info),
                    max_messages=max_messages - len(messages),
                )
            )
    return messages[:max_messages]


def _parse_mbox_bytes(raw: bytes, *, max_messages: int) -> list[ApplicationEmailMessage]:
    messages: list[ApplicationEmailMessage] = []
    with tempfile.TemporaryDirectory(prefix="jsa-email-import-") as directory:
        path = Path(directory) / "messages.mbox"
        path.write_bytes(raw)
        imported = mailbox.mbox(str(path), create=False)
        try:
            for message in imported:
                if len(messages) >= max_messages:
                    break
                parsed = message_from_bytes(message.as_bytes(), policy=policy.default)
                messages.extend(
                    _parse_email_message(
                        parsed,
                        max_messages=max_messages - len(messages),
                    )
                )
        finally:
            imported.close()
    return messages[:max_messages]


def _is_unsafe_path(filename: str) -> bool:
    parts = filename.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        return True
    return bool(filename.startswith("/"))
