from pathlib import Path

from app.observability.logging import redact_sensitive_fields
from app.prompts.registry import PromptDefinition, PromptRegistry


def test_prompt_registry_loads_and_renders_active_version() -> None:
    registry_path = Path(__file__).parents[2] / "prompts" / "registry.json"
    registry = PromptRegistry.load(registry_path)

    rendered = registry.render(
        "vacancy_match",
        {"vacancy_title": "Engineer", "verified_facts": "Python"},
    )

    assert "Engineer" in rendered
    assert "Python" in rendered


def test_prompt_registry_rejects_multiple_active_versions() -> None:
    first = PromptDefinition(
        name="test",
        purpose="test",
        version=1,
        template="Hello",
        input_schema="Input",
        output_schema="Output",
        model_task_class="low_cost",
        created_on="2026-07-18",
        is_active=True,
    )
    second = first.model_copy(update={"version": 2})

    try:
        PromptRegistry((first, second))
    except ValueError as error:
        assert "Multiple active versions" in str(error)
    else:
        raise AssertionError("Multiple active prompt versions were accepted")


def test_structured_logging_redacts_secret_like_keys() -> None:
    event = {"event": "provider_call", "api_key": "secret", "session_token": "token"}

    redacted = redact_sensitive_fields(None, "info", event)

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["session_token"] == "[REDACTED]"
