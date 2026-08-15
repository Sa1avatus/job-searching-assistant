import asyncio

from app.services.http_adapters import HttpHeadHunterAdapter, HttpLinkedInAdapter


class _SubmissionProbeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def has_submitted_application(
        self,
        *,
        source: str,
        user_id: str,
        url: str,
    ) -> bool:
        self.calls.append((source, user_id, url))
        return source == "headhunter"


def test_http_browser_adapters_delegate_submission_probes() -> None:
    async def run_test() -> None:
        client = _SubmissionProbeClient()
        headhunter = HttpHeadHunterAdapter(client, "user-1")  # type: ignore[arg-type]
        linkedin = HttpLinkedInAdapter(client, "user-1")  # type: ignore[arg-type]

        assert await headhunter.has_submitted_application("https://hh.ru/vacancy/1") is True
        assert await linkedin.has_submitted_application("https://linkedin.com/jobs/view/2") is False
        assert client.calls == [
            ("headhunter", "user-1", "https://hh.ru/vacancy/1"),
            ("linkedin", "user-1", "https://linkedin.com/jobs/view/2"),
        ]

    asyncio.run(run_test())
