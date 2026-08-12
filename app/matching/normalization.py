from __future__ import annotations

from dataclasses import dataclass

_SKILL_ALIASES: dict[str, str] = {
    # Databases
    "postgres": "postgresql",
    "pgsql": "postgresql",
    "psql": "postgresql",
    "pg": "postgresql",
    "mariadb": "mysql",
    # Containers / orchestration
    "k8s": "kubernetes",
    "k8": "kubernetes",
    "docker compose": "docker",
    "podman": "docker",
    # Languages / runtimes
    "golang": "go",
    "rs": "rust",
    "ts": "typescript",
    "js": "javascript",
    "py": "python",
    "node": "node.js",
    "nodejs": "node.js",
    # ML / AI
    "torch": "pytorch",
    "tf": "tensorflow",
    "sklearn": "scikit-learn",
    "hf": "huggingface",
    "hugging face": "huggingface",
    # Cloud
    "aws": "amazon web services",
    "gcp": "google cloud platform",
    "azure": "microsoft azure",
    # Frameworks
    "fast api": "fastapi",
    "fast-api": "fastapi",
    "springboot": "spring boot",
    "spring-boot": "spring boot",
    # Messaging
    "amqp": "rabbitmq",
    # Search
    "es": "elasticsearch",
    "elastic": "elasticsearch",
    "os": "opensearch",
}


@dataclass(frozen=True, slots=True)
class NormalizedSkill:
    canonical: str
    original: str
    confidence: float


class SkillNormalizer:
    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        self._aliases = {**_SKILL_ALIASES, **(aliases or {})}

    def normalize(self, skill_name: str) -> NormalizedSkill:
        canonical = self._aliases.get(skill_name.casefold().strip(), skill_name)
        return NormalizedSkill(
            canonical=canonical,
            original=skill_name,
            confidence=1.0 if canonical != skill_name else 0.8,
        )

    def normalize_many(self, skill_names: tuple[str, ...]) -> tuple[NormalizedSkill, ...]:
        return tuple(self.normalize(name) for name in skill_names)
