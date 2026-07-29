"""Deterministic vacancy presentation metadata helpers."""

from __future__ import annotations

import re
from typing import Literal

_HYBRID_PATTERNS = [
    re.compile(r"\bhybrid\b", re.IGNORECASE),
    re.compile(r"\bhybrid\s+work\b", re.IGNORECASE),
    re.compile(r"\bhybrid\s+schedule\b", re.IGNORECASE),
    re.compile(r"\bгибрид\b", re.IGNORECASE),
    re.compile(r"\bгибридн\w*\b", re.IGNORECASE),
]

_REMOTE_PATTERNS = [
    re.compile(r"\bremote\b", re.IGNORECASE),
    re.compile(r"\bremote\s+work\b", re.IGNORECASE),
    re.compile(r"\bremote\s+position\b", re.IGNORECASE),
    re.compile(r"\bwork\s+from\s+home\b", re.IGNORECASE),
    re.compile(r"\bfully\s+remote\b", re.IGNORECASE),
    re.compile(r"\b100\s*%\s*remote\b", re.IGNORECASE),
    re.compile(r"\bудалённ\w*\b", re.IGNORECASE),
    re.compile(r"\bудалёнка\b", re.IGNORECASE),
    re.compile(r"\bудаленн\w*\b", re.IGNORECASE),
    re.compile(r"\bудаленка\b", re.IGNORECASE),
    re.compile(r"\bдистанцион\w*\b", re.IGNORECASE),
    re.compile(r"\bиз\s+дома\b", re.IGNORECASE),
    re.compile(r"\bна\s+дому\b", re.IGNORECASE),
]

_OFFICE_PATTERNS = [
    re.compile(r"\boffice\b", re.IGNORECASE),
    re.compile(r"\bon-?site\b", re.IGNORECASE),
    re.compile(r"\bin-?office\b", re.IGNORECASE),
    re.compile(r"\boffice\s+work\b", re.IGNORECASE),
    re.compile(r"\bat\s+the\s+employer(?:'s|’s)?\s+location\b", re.IGNORECASE),
    re.compile(r"\bat\s+the\s+workplace\b", re.IGNORECASE),
    re.compile(r"\bофис\b", re.IGNORECASE),
    re.compile(r"\bв\s+офисе\b", re.IGNORECASE),
    re.compile(r"\bна\s+месте\b", re.IGNORECASE),
    re.compile(r"\bна\s+территории\s+работодателя\b", re.IGNORECASE),
]

_INFORMATIVE_MIN_LENGTH = 20

_KEY_SKILL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Python", re.compile(r"(?<!\w)python(?!\w)", re.IGNORECASE)),
    ("Java", re.compile(r"(?<!\w)java(?!script)(?!\w)", re.IGNORECASE)),
    ("JavaScript", re.compile(r"(?<!\w)javascript(?!\w)", re.IGNORECASE)),
    ("TypeScript", re.compile(r"(?<!\w)typescript(?!\w)", re.IGNORECASE)),
    ("C#", re.compile(r"(?<!\w)c#(?!\w)", re.IGNORECASE)),
    ("C++", re.compile(r"(?<!\w)c\+\+(?!\w)", re.IGNORECASE)),
    (
        "Go",
        re.compile(
            r"(?<!\w)golang(?!\w)"
            r"|(?<!\w)go\s+(?:programming(?:\s+language)?|developers?|engineers?|backend|development|code|services?|microservices?)(?!\w)"
            r"|(?<!\w)(?:experience\s+with|proficiency\s+in|development\s+in|written\s+in|using)\s+go(?!\w)",
            re.IGNORECASE,
        ),
    ),
    ("SQL", re.compile(r"(?<!\w)sql(?!\w)", re.IGNORECASE)),
    ("PostgreSQL", re.compile(r"(?<!\w)postgres(?:ql)?(?!\w)", re.IGNORECASE)),
    ("MySQL", re.compile(r"(?<!\w)mysql(?!\w)", re.IGNORECASE)),
    ("NoSQL", re.compile(r"(?<!\w)nosql(?!\w)", re.IGNORECASE)),
    ("Redis", re.compile(r"(?<!\w)redis(?!\w)", re.IGNORECASE)),
    ("RabbitMQ", re.compile(r"(?<!\w)rabbitmq(?!\w)", re.IGNORECASE)),
    ("Kafka", re.compile(r"(?<!\w)kafka(?!\w)", re.IGNORECASE)),
    ("REST API", re.compile(r"(?<!\w)rest(?:ful)?\s+api(?:s)?(?!\w)", re.IGNORECASE)),
    ("SOAP API", re.compile(r"(?<!\w)soap\s+api(?:s)?(?!\w)", re.IGNORECASE)),
    ("FastAPI", re.compile(r"(?<!\w)fastapi(?!\w)", re.IGNORECASE)),
    ("Django", re.compile(r"(?<!\w)django(?!\w)", re.IGNORECASE)),
    ("Docker", re.compile(r"(?<!\w)docker(?!\w)", re.IGNORECASE)),
    ("Kubernetes", re.compile(r"(?<!\w)kubernetes(?!\w)|(?<!\w)k8s(?!\w)", re.IGNORECASE)),
    ("Terraform", re.compile(r"(?<!\w)terraform(?!\w)", re.IGNORECASE)),
    ("AWS", re.compile(r"(?<!\w)aws(?!\w)", re.IGNORECASE)),
    ("Azure", re.compile(r"(?<!\w)azure(?!\w)", re.IGNORECASE)),
    ("GCP", re.compile(r"(?<!\w)gcp(?!\w)|google\s+cloud", re.IGNORECASE)),
    ("CI/CD", re.compile(r"(?<!\w)ci\s*/\s*cd(?!\w)", re.IGNORECASE)),
    ("Linux", re.compile(r"(?<!\w)linux(?!\w)", re.IGNORECASE)),
    ("Git", re.compile(r"(?<!\w)git(?!\w)", re.IGNORECASE)),
    ("TensorFlow", re.compile(r"(?<!\w)tensorflow(?!\w)", re.IGNORECASE)),
    ("PyTorch", re.compile(r"(?<!\w)pytorch(?!\w)", re.IGNORECASE)),
    ("Keras", re.compile(r"(?<!\w)keras(?!\w)", re.IGNORECASE)),
    ("Airflow", re.compile(r"(?<!\w)airflow(?!\w)", re.IGNORECASE)),
    ("MLflow", re.compile(r"(?<!\w)mlflow(?!\w)", re.IGNORECASE)),
    ("Kubeflow", re.compile(r"(?<!\w)kubeflow(?!\w)", re.IGNORECASE)),
    ("LLM", re.compile(r"(?<!\w)llms?(?!\w)|large\s+language\s+models?", re.IGNORECASE)),
    ("RAG", re.compile(r"(?<!\w)rag(?!\w)|retrieval[- ]augmented", re.IGNORECASE)),
    ("Machine Learning", re.compile(r"\bmachine\s+learning\b", re.IGNORECASE)),
    ("Deep Learning", re.compile(r"\bdeep\s+learning\b", re.IGNORECASE)),
    ("NLP", re.compile(r"(?<!\w)nlp(?!\w)|natural\s+language\s+processing", re.IGNORECASE)),
    ("Generative AI", re.compile(r"\bgenerative\s+ai\b|(?<!\w)genai(?!\w)", re.IGNORECASE)),
    ("Data Science", re.compile(r"\bdata\s+science\b", re.IGNORECASE)),
    ("Data Governance", re.compile(r"\bdata\s+governance\b", re.IGNORECASE)),
    ("Enterprise Architecture", re.compile(r"\benterprise\s+architecture\b", re.IGNORECASE)),
    ("Distributed Systems", re.compile(r"\bdistributed\s+systems?\b", re.IGNORECASE)),
    ("APIs", re.compile(r"(?<!\w)apis?(?!\w)", re.IGNORECASE)),
    ("Message Queues", re.compile(r"\bmessage\s+queues?\b", re.IGNORECASE)),
    (
        "Event-driven Architecture",
        re.compile(r"\bevent[- ]driven\s+architectures?\b", re.IGNORECASE),
    ),
    ("Data Modeling", re.compile(r"\bdata\s+modelling\b|\bdata\s+modeling\b", re.IGNORECASE)),
    (
        "Cloud Architecture",
        re.compile(r"\bcloud[- ]native\b|\bcloud\s+architectures?\b", re.IGNORECASE),
    ),
    ("DevSecOps", re.compile(r"(?<!\w)devsecops(?!\w)", re.IGNORECASE)),
    (
        "Infrastructure as Code",
        re.compile(
            r"\binfrastructure[- ]as[- ]code\b|(?<!\w)iac(?!\w)",
            re.IGNORECASE,
        ),
    ),
    ("Observability", re.compile(r"(?<!\w)observability(?!\w)", re.IGNORECASE)),
    ("OWASP", re.compile(r"(?<!\w)owasp(?!\w)", re.IGNORECASE)),
    ("Spring", re.compile(r"\bspring(?:\s+ecosystem|\s+framework|\s+boot)?\b", re.IGNORECASE)),
    ("Information Security", re.compile(r"\binformation\s+security\b", re.IGNORECASE)),
    ("TOGAF", re.compile(r"(?<!\w)togaf(?!\w)", re.IGNORECASE)),
)


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip."""
    return " ".join(text.split())


def _first_sentence(text: str) -> str | None:
    """Return the first complete sentence (ending with ``.!?``), or ``None``."""
    match = re.search(r"[.!?](?:\s|$)", text)
    if match is not None:
        return text[: match.end()].rstrip()
    return None


def summarize_vacancy(description_text: str, max_characters: int = 360) -> str:
    """Return a deterministic vacancy summary of at most *max_characters*.

    Normalizes whitespace, inspects the first complete sentence, and returns
    it when it is informative and within the limit.  Otherwise the whole
    normalized text is returned when it fits; if it does not, the text is
    truncated on a word boundary and a single ellipsis is appended.

    Empty descriptions return an empty string.

    Raises :class:`ValueError` when *max_characters* is not positive.
    """
    if max_characters <= 0:
        raise ValueError("max_characters must be positive")

    requirement_text = _extract_requirement_section(description_text)
    normalized = _normalize_whitespace(requirement_text or description_text)
    if not normalized:
        return ""

    first = _first_sentence(normalized)
    if (
        not requirement_text
        and first is not None
        and len(first) >= _INFORMATIVE_MIN_LENGTH
        and len(first) <= max_characters
    ):
        return first

    if len(normalized) <= max_characters:
        return normalized

    ellipsis = "\u2026"
    available = max_characters - len(ellipsis)
    if available <= 0:
        return ellipsis[:max_characters]
    truncated = normalized[:available]
    if not normalized[available : available + 1].isspace():
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
    return truncated.rstrip() + ellipsis


def _extract_requirement_section(description_text: str) -> str:
    """Extract the useful requirement block from a structured vacancy."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in description_text.splitlines()]
    lines = [line for line in lines if line]
    start_markers = {
        "requirements",
        "what we're looking for",
        "what we’re looking for",
        "qualifications",
        "required qualifications",
        "требования",
    }
    stop_markers = {
        "benefits",
        "what we offer",
        "our engineering principles",
        "about us",
        "о компании",
        "условия",
    }
    start = next(
        (index for index, line in enumerate(lines) if line.casefold() in start_markers),
        None,
    )
    if start is None:
        return ""
    selected: list[str] = []
    ignored_headings = {
        "engineering foundation :",
        "engineering foundation:",
        "product & leadership:",
        "nice-to-have",
        "nice to have",
    }
    for line in lines[start + 1 :]:
        folded = line.casefold()
        if folded in stop_markers:
            break
        if folded in start_markers or folded in ignored_headings:
            continue
        selected.append(line)
    return " ".join(selected)


def detect_work_format(
    title: str,
    location: str,
    description_text: str,
) -> Literal["remote", "hybrid", "office", "unspecified"]:
    """Classify work format from title, location, and description.

    Hybrid indicators take precedence over remote and office.
    Remote indicators take precedence over office.
    Word and phrase boundaries are used to avoid substring false positives.
    """
    combined = f"{title} {location} {description_text}"
    if any(p.search(combined) for p in _HYBRID_PATTERNS):
        return "hybrid"
    if any(p.search(combined) for p in _REMOTE_PATTERNS):
        return "remote"
    if any(p.search(combined) for p in _OFFICE_PATTERNS):
        return "office"
    return "unspecified"


def extract_key_skills(
    description_text: str,
    declared_skills: list[str] | tuple[str, ...] = (),
    *,
    limit: int = 20,
) -> tuple[str, ...]:
    """Return stable display tags from declared skills and explicit description mentions."""
    if limit < 1:
        return ()
    skills: list[str] = []
    seen: set[str] = set()
    for skill in declared_skills:
        normalized = " ".join(skill.split()).strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            skills.append(normalized)
            if len(skills) == limit:
                return tuple(skills)
    for label, pattern in _KEY_SKILL_PATTERNS:
        if label.casefold() in seen or not pattern.search(description_text):
            continue
        seen.add(label.casefold())
        skills.append(label)
        if len(skills) == limit:
            break
    return tuple(skills)
