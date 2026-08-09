import asyncio
from email.message import EmailMessage

from app.services.email_integrations import EmailIntegration
from app.services.imap_email_provider import ImapApplicationEmailProvider


class FakeImapClient:
    def __init__(self, raw_messages: dict[bytes, bytes]) -> None:
        self.raw_messages = raw_messages
        self.selected: tuple[str, bool] | None = None
        self.fetch_calls: list[tuple[bytes, str]] = []
        self.logged_out = False

    def login(self, username: str, password: str) -> tuple[str, list[bytes]]:
        assert username == "candidate@example.test"
        assert password == "app-password"
        return "OK", [b"logged in"]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected = (mailbox, readonly)
        return "OK", [b"2"]

    def search(self, charset: str | None, *criteria: str) -> tuple[str, list[bytes]]:
        assert charset is None
        assert criteria == ("UNSEEN",)
        return "OK", [b"1 2"]

    def fetch(self, message_set: bytes, message_parts: str) -> tuple[str, list[object]]:
        self.fetch_calls.append((message_set, message_parts))
        return "OK", [(b"response", self.raw_messages[message_set])]

    def logout(self) -> tuple[str, list[bytes]]:
        self.logged_out = True
        return "BYE", [b"logged out"]


def _raw_email(subject: str, body: str) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message.set_content(body)
    return message.as_bytes()


def test_imap_provider_reads_unseen_messages_without_marking_them_seen() -> None:
    client = FakeImapClient(
        {
            b"1": _raw_email("Application update", "We will not be moving forward."),
            b"2": _raw_email("Next stage", "Please schedule an interview."),
        }
    )
    integration = EmailIntegration(
        user_id="user-1",
        host="imap.example.test",
        port=993,
        username="candidate@example.test",
        password="app-password",
        use_ssl=True,
        mailbox="INBOX",
        enabled=True,
    )
    provider = ImapApplicationEmailProvider(
        integration,
        client_factory=lambda host, port, use_ssl: client,
    )

    messages = asyncio.run(provider.fetch_messages())

    assert [message.subject for message in messages] == ["Application update", "Next stage"]
    assert "schedule an interview" in messages[1].body
    assert client.selected == ("INBOX", True)
    assert client.fetch_calls == [(b"1", "(BODY.PEEK[])"), (b"2", "(BODY.PEEK[])")]
    assert client.logged_out is True


def test_imap_provider_limits_fetch_to_most_recent_message_ids() -> None:
    client = FakeImapClient(
        {
            b"1": _raw_email("Old", "Old message"),
            b"2": _raw_email("Recent", "Recent message"),
        }
    )
    integration = EmailIntegration(
        user_id="user-1",
        host="imap.example.test",
        port=993,
        username="candidate@example.test",
        password="app-password",
        use_ssl=True,
        mailbox="INBOX",
        enabled=True,
    )
    provider = ImapApplicationEmailProvider(
        integration,
        max_messages=1,
        client_factory=lambda host, port, use_ssl: client,
    )

    messages = asyncio.run(provider.fetch_messages())

    assert [message.subject for message in messages] == ["Recent"]
    assert client.fetch_calls == [(b"2", "(BODY.PEEK[])")]
