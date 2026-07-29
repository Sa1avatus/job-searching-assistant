from __future__ import annotations

from unittest.mock import MagicMock

from app.domain.models import TaskState
from app.services.recruitment import RecruitmentService


def test_failed_real_submission_can_be_scheduled_again() -> None:
    application = MagicMock()
    application.status = "awaiting_review"
    application.vacancy_id = "vacancy-1"
    application.answers = []

    vacancy = MagicMock()
    vacancy.adapter_name = "headhunter"

    task = MagicMock()
    task.state = TaskState.FAILED.value
    task.attempt_number = 1
    task.transitions = []

    session = MagicMock()
    session.scalar.side_effect = [application, task]
    session.get.return_value = vacancy

    result = RecruitmentService(session).schedule_real_submission_apply(
        "application-1",
        adapter_name="headhunter",
        workflow="headhunter_apply",
        site_key="hh.ru",
    )

    assert result is task
    assert task.state == TaskState.SCHEDULED.value
    assert task.task_payload == {"workflow": "headhunter_apply"}
    assert task.transitions[-1].previous_state == TaskState.FAILED.value
    assert task.transitions[-1].new_state == TaskState.SCHEDULED.value
    session.commit.assert_called_once_with()
