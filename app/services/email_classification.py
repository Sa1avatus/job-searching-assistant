"""LLM-backed classification of employer emails about job applications.

The deterministic regex classifier (:mod:`app.services.application_email_classifier`) is the
fallback; this service produces a richer result — a fine-grained category, a confidence
score, and any company/vacancy title mentioned — by asking the user's configured LLM.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.application_email import EmailCategory
from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.prompts.registry import PromptRegistry

_MAX_SUBJECT_CHARS = 500
_MAX_BODY_CHARS = 4_000

_PROMPT_NAME = "classify_application_email"


class EmailClassification(BaseModel):
    """Structured output of email classification."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: str
    confidence: float = Field(ge=0, le=1)
    company: str | None = None
    vacancy_title: str | None = None
    summary: str = ""

    @field_validator("category")
    @classmethod
    def _normalize_category(cls, value: str) -> str:
        try:
            return EmailCategory(value.strip().casefold()).value
        except ValueError:
            return EmailCategory.OTHER.value


class EmailClassifier:
    """Classify an application email using the configured LLM via the model router."""

    def __init__(self, router: ModelRouter, prompt_registry: PromptRegistry) -> None:
        self._router = router
        self._prompt_registry = prompt_registry

    async def classify(self, subject: str, body: str) -> EmailClassification:
        prompt = self._prompt_registry.render(
            _PROMPT_NAME,
            {
                "subject": subject[:_MAX_SUBJECT_CHARS],
                "body": _truncate(body, _MAX_BODY_CHARS),
            },
        )
        return await self._router.route(
            ModelRequest(
                task_name=_PROMPT_NAME,
                task_class=ModelTaskClass.LOW_COST,
                prompt=prompt,
                max_cost_usd=0.05,
                timeout_seconds=90,
            ),
            EmailClassification,
        )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n\n[...omitted...]\n\n" + text[-half:]
