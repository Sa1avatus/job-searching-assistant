"""Verify durable API-to-SQL-to-Redis-to-worker dispatch in Compose."""

from __future__ import annotations

import time
import uuid

import httpx


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
        user_response = client.post("/v1/users", json={"display_name": "Dispatcher smoke"})
        user_response.raise_for_status()
        user = user_response.json()
        try:
            vacancy_response = client.post(
                "/v1/vacancies",
                json={
                    "source_url": f"https://example.test/jobs/{uuid.uuid4()}",
                    "title": "Dispatcher smoke vacancy",
                    "company": "Controlled test",
                    "required_skills": [],
                },
            )
            vacancy_response.raise_for_status()
            application_response = client.post(
                "/v1/applications/prepare",
                json={
                    "user_id": user["id"],
                    "vacancy_id": vacancy_response.json()["id"],
                },
            )
            application_response.raise_for_status()
            application = application_response.json()

            deadline = time.monotonic() + 15
            while True:
                task_response = client.get(f"/v1/applications/{application['id']}/task")
                task_response.raise_for_status()
                task = task_response.json()
                if task["state"] == "waiting_for_user":
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Dispatcher did not finish task; last state={task['state']}"
                    )
                time.sleep(0.25)

            states = [transition["new_state"] for transition in task["transitions"]]
            assert states == ["scheduled", "running", "waiting_for_user"]
            assert task["attempt_number"] == 1
            print("Dispatcher smoke passed: scheduled -> running -> waiting_for_user")
        finally:
            delete_response = client.delete(f"/v1/users/{user['id']}")
            delete_response.raise_for_status()


if __name__ == "__main__":
    main()
