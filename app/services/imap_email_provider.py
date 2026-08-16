from __future__ import annotations

import asyncio
import imaplib
from collections.abc import Callable
from contextlib import suppress
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import EmailMessage
from typing import Protocol, cast

from app.services.application_email_sync import ApplicationEmailMessage
from app.services.email_integrations import EmailIntegration


class ImapClient(Protocol):
    def login(self, username: str, password: str) -> tuple[str, list[bytes]]: ...

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]: ...

    def search(self, charset: str | None, *criteria: str) -> tuple[str, list[bytes]]: ...

    def fetch(self, message_set: bytes, message_parts: str) -> tuple[str, list[object]]: ...

    def logout(self) -> tuple[str, list[bytes]]: ...


ImapClientFactory = Callable[[str, int, bool], ImapClient]


class ImapApplicationEmailProvider:
    def __init__(
        self,
        integration: EmailIntegration,
        *,
        max_messages: int = 50,
        client_factory: ImapClientFactory | None = None,
    ) -> None:
        if max_messages < 1 or max_messages > 200:
            raise ValueError("IMAP message limit must be between 1 and 200")
        self._integration = integration
        self._max_messages = max_messages
        self._client_factory = client_factory or _open_imap_client

    async def fetch_messages(self) -> list[ApplicationEmailMessage]:
        return await asyncio.to_thread(self._fetch_messages)

    def _fetch_messages(self) -> list[ApplicationEmailMessage]:
        config = self._integration
        client = self._client_factory(config.host, config.port, config.use_ssl)
        try:
            _require_ok(client.login(config.username, config.password), "IMAP login failed")
            _require_ok(
                client.select(config.mailbox, readonly=True),
                "IMAP mailbox cannot be opened",
            )
            _, search_data = _require_ok(
                client.search(None, "ALL"),
                "IMAP search failed",
            )
            message_ids = search_data[0].split()[-self._max_messages :] if search_data else []
            messages: list[ApplicationEmailMessage] = []
            for message_id in message_ids:
                _, fetch_data = _require_ok(
                    client.fetch(message_id, "(BODY.PEEK[])"),
                    "IMAP message fetch failed",
                )
                raw_message = _raw_message_bytes(fetch_data)
                if raw_message is None:
                    continue
                message = message_from_bytes(raw_message, policy=policy.default)
                messages.append(
                    ApplicationEmailMessage(
                        subject=_decode_header_value(message.get("Subject", "")),
                        body=_plain_text_body(message),
                    )
                )
            return messages
        finally:
            with suppress(imaplib.IMAP4.error):
                client.logout()


def _open_imap_client(host: str, port: int, use_ssl: bool) -> ImapClient:
    if use_ssl:
        return cast(ImapClient, imaplib.IMAP4_SSL(host, port))
    return cast(ImapClient, imaplib.IMAP4(host, port))


def _require_ok[ResponseData](
    response: tuple[str, ResponseData],
    message: str,
) -> tuple[str, ResponseData]:
    if response[0] != "OK":
        raise imaplib.IMAP4.error(message)
    return response


def _raw_message_bytes(fetch_data: list[object]) -> bytes | None:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _decode_header_value(value: str) -> str:
    decoded: list[str] = []
    for fragment, charset in decode_header(value):
        if isinstance(fragment, bytes):
            decoded.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(fragment)
    return "".join(decoded)


def _plain_text_body(message: EmailMessage) -> str:
    if message.is_multipart():
        parts = [
            part.get_content()
            for part in message.walk()
            if part.get_content_type() == "text/plain"
            and part.get_content_disposition() != "attachment"
        ]
        return "\n".join(part for part in parts if isinstance(part, str))
    content = message.get_content()
    return content if isinstance(content, str) else ""
