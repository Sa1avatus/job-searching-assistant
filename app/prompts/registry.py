import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field


class PromptDefinition(BaseModel):
    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    version: int = Field(ge=1)
    template: str = Field(min_length=1)
    input_schema: str = Field(min_length=1)
    output_schema: str = Field(min_length=1)
    model_task_class: str = Field(pattern="^(low_cost|strong_reasoning|embedding)$")
    created_on: date
    is_active: bool = False


class PromptRegistry:
    def __init__(self, definitions: tuple[PromptDefinition, ...]) -> None:
        definition_by_key: dict[tuple[str, int], PromptDefinition] = {}
        active_by_name: dict[str, PromptDefinition] = {}
        for definition in definitions:
            key = (definition.name, definition.version)
            if key in definition_by_key:
                raise ValueError(
                    f"Duplicate prompt version: {definition.name}:{definition.version}"
                )
            definition_by_key[key] = definition
            if definition.is_active:
                if definition.name in active_by_name:
                    raise ValueError(f"Multiple active versions: {definition.name}")
                active_by_name[definition.name] = definition
        self._definition_by_key = definition_by_key
        self._active_by_name = active_by_name

    @classmethod
    def load(cls, registry_path: Path) -> "PromptRegistry":
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        definitions = tuple(PromptDefinition.model_validate(item) for item in payload["prompts"])
        return cls(definitions)

    def get(self, name: str, version: int | None = None) -> PromptDefinition:
        if version is None:
            definition = self._active_by_name.get(name)
        else:
            definition = self._definition_by_key.get((name, version))
        if definition is None:
            raise KeyError(f"Prompt not found: {name}:{version or 'active'}")
        return definition

    def render(self, name: str, variables: dict[str, str], version: int | None = None) -> str:
        definition = self.get(name, version)
        try:
            return definition.template.format_map(variables)
        except KeyError as error:
            raise ValueError(f"Missing prompt variable: {error.args[0]}") from error
