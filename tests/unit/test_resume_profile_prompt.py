from app.prompts.resume_profile import build_resume_profile_prompt


def test_resume_profile_prompt_keeps_beginning_and_end_within_limit() -> None:
    resume_text = "A" * 30_000 + "B" * 30_000

    prompt = build_resume_profile_prompt(resume_text)

    assert "A" * 100 in prompt
    assert "B" * 100 in prompt
    assert "[... middle of resume omitted for length ...]" in prompt
    assert len(prompt) < len(resume_text)
