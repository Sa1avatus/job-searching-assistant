"""Read one public hh.ru vacancy without authentication or application submission."""

from __future__ import annotations

import argparse
import asyncio

import httpx

from adapters.job_boards.headhunter_api import HeadHunterApi
from app.config import get_settings


async def run(vacancy_url: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        vacancy = await HeadHunterApi(
            client,
            user_agent=settings.hh_user_agent,
            access_token=(
                settings.hh_access_token.get_secret_value()
                if settings.hh_access_token is not None
                else None
            ),
        ).extract_vacancy(vacancy_url)
    print(
        f"HeadHunter read smoke passed: id={vacancy.evidence_api_url.rsplit('/', 1)[-1]} "
        f"title={vacancy.title!r} company={vacancy.company!r}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("vacancy_url")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.vacancy_url))


if __name__ == "__main__":
    main()
