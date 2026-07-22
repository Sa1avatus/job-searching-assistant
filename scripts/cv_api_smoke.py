"""Exercise the CV upload and review path against a running API."""

from __future__ import annotations

import argparse
import uuid

import httpx


def run(base_url: str) -> None:
    with httpx.Client(base_url=base_url, timeout=10) as client:
        user_response = client.post("/v1/users", json={"display_name": "CV smoke"})
        user_response.raise_for_status()
        user = user_response.json()

        upload_response = client.post(
            f"/v1/users/{user['id']}/cv-files",
            files={"file": ("resume.pdf", b"%PDF-1.7\n%%EOF", "application/pdf")},
        )
        upload_response.raise_for_status()
        cv_file = upload_response.json()

        vacancy_response = client.post(
            "/v1/vacancies",
            json={
                "source_url": f"https://example.test/jobs/{uuid.uuid4()}",
                "title": "Smoke Engineer",
                "company": "Smoke",
                "required_skills": [],
            },
        )
        vacancy_response.raise_for_status()

        application_response = client.post(
            "/v1/applications/prepare",
            json={
                "user_id": user["id"],
                "vacancy_id": vacancy_response.json()["id"],
                "cv_file_id": cv_file["id"],
            },
        )
        application_response.raise_for_status()
        application = application_response.json()

        queue_response = client.get("/v1/review-queue")
        queue_response.raise_for_status()
        queue_item = next(item for item in queue_response.json() if item["id"] == application["id"])

        delete_response = client.delete(f"/v1/users/{user['id']}")
        delete_response.raise_for_status()

    assert application["selected_cv_file_id"] == cv_file["id"]
    assert queue_item["selected_cv_filename"] == "resume.pdf"
    print("CV API smoke passed: upload, selection, review evidence, and deletion")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()
    run(arguments.base_url)


if __name__ == "__main__":
    main()
