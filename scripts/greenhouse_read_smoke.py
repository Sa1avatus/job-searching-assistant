import argparse
import asyncio

import httpx

from adapters.job_boards.greenhouse_api import GreenhouseJobBoardApi


async def extract(url: str) -> None:
    timeout = httpx.Timeout(30)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        job = await GreenhouseJobBoardApi(client).extract_job(url)
    print(
        f"greenhouse-read-smoke: ok id={job.job_id} "
        f"title={job.title!r} fields={len(job.form_fields)} "
        f"sensitive_review={job.requires_sensitive_review}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    args = parser.parse_args()
    asyncio.run(extract(args.url))


if __name__ == "__main__":
    main()
