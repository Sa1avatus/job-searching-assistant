import asyncio
from pathlib import Path

from adapters.job_boards.greenhouse import GreenhouseAdapter, normalize_greenhouse_resume_path
from app.browser.engine import PlaywrightEngine


class ControlledGreenhouseAdapter(GreenhouseAdapter):
    """Test-only host boundary override for a packaged local fixture."""

    def supports_url(self, url: str) -> bool:
        return url.startswith("file:")


def test_greenhouse_prepares_validated_review_without_submitting(tmp_path: Path) -> None:
    async def run_flow() -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "application.html"
        resume_path = tmp_path / "resume.pdf"
        resume_path.write_bytes(b"%PDF-1.4\n% controlled test resume\n")
        async with PlaywrightEngine(artifact_directory=tmp_path / "artifacts") as engine:
            adapter = ControlledGreenhouseAdapter(engine)
            result = await adapter.prepare_review(
                fixture_path.resolve().as_uri(),
                {
                    "full-name": "Controlled Candidate",
                    "email": "candidate@example.test",
                    "resume": normalize_greenhouse_resume_path(resume_path),
                    "authorization": "Yes",
                },
            )

            assert result.is_ready_for_review
            assert result.actions[-1].action_name == "checkpoint"
            assert result.actions[-1].screenshot_path is not None
            assert {action.action_name for action in result.actions} <= {"fill", "checkpoint"}

    asyncio.run(run_flow())


def test_greenhouse_rejects_unknown_answers_and_missing_required_fields(tmp_path: Path) -> None:
    async def run_flow() -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "application.html"
        async with PlaywrightEngine(artifact_directory=tmp_path) as engine:
            adapter = ControlledGreenhouseAdapter(engine)
            try:
                await adapter.prepare_review(fixture_path.resolve().as_uri(), {"invented": "value"})
            except ValueError as error:
                assert "unknown field IDs" in str(error)
            else:
                raise AssertionError("Unknown form answers must be rejected")

            result = await adapter.prepare_review(fixture_path.resolve().as_uri(), {})
            assert result.is_ready_for_review is False
            assert any(action.error_category is not None for action in result.actions)
            assert all(action.action_name != "checkpoint" for action in result.actions)

    asyncio.run(run_flow())


def test_greenhouse_empty_form_fails_closed(tmp_path: Path) -> None:
    async def run_flow() -> None:
        fixture_path = tmp_path / "empty-form.html"
        fixture_path.write_text(
            "<!doctype html><title>No application form</title>", encoding="utf-8"
        )
        async with PlaywrightEngine(artifact_directory=tmp_path / "artifacts") as engine:
            result = await ControlledGreenhouseAdapter(engine).prepare_review(
                fixture_path.resolve().as_uri(), {}
            )

        assert result.fields == ()
        assert result.actions == ()
        assert result.is_ready_for_review is False

    asyncio.run(run_flow())
