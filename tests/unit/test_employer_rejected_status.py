from pathlib import Path

from app.domain.application_status import APPLICATION_STATUSES, TERMINAL_STATUSES


def test_candidate_and_employer_rejections_are_distinct_terminal_statuses() -> None:
    assert "rejected" in APPLICATION_STATUSES
    assert "employer_rejected" in APPLICATION_STATUSES
    assert {"rejected", "employer_rejected"} <= TERMINAL_STATUSES


def test_migration_reclassifies_only_email_applied_rejections() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "versions" / "0032_employer_rejected_status.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision = "0031"' in migration
    assert "SET status = 'employer_rejected'" in migration
    assert "application_email_events.status_applied = true" in migration
    assert "application_email_events.outcome = 'rejected'" in migration
    assert "source = 'email_event'" in migration
