"""Verify local LinkedIn reference import without contacting LinkedIn."""

from __future__ import annotations

import time

import httpx


def main() -> None:
    job_id = str(time.time_ns())
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
        response = client.post(
            "/v1/vacancies/import-linkedin-reference",
            json={
                "source_url": f"https://www.linkedin.com/jobs/view/smoke-{job_id}",
                "title": "Reference smoke vacancy",
                "company": "Local controlled test",
                "description_text": "User-supplied metadata; no LinkedIn network request",
            },
        )
        response.raise_for_status()
        vacancy = response.json()
    assert vacancy["adapter_name"] == "linkedin-reference"
    assert vacancy["source_url"] == f"https://www.linkedin.com/jobs/view/{job_id}"
    print("LinkedIn reference smoke passed without contacting LinkedIn")


if __name__ == "__main__":
    main()
