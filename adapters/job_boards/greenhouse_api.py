from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, Field, field_validator

from app.browser.form_discovery import classify_semantic_category
from app.domain.forms import FormField, FormFieldType


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def html_to_text(content: str) -> str:
    parser = _TextExtractor()
    parser.feed(html.unescape(html.unescape(content)))
    return "\n".join(parser.parts)


@dataclass(frozen=True, slots=True)
class GreenhouseJobReference:
    board_token: str
    job_id: int
    source_url: str

    @classmethod
    def from_url(cls, url: str) -> GreenhouseJobReference:
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https":
            raise ValueError("Greenhouse URL must use HTTPS")
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ValueError("Greenhouse URL must not contain credentials")
        try:
            port = parsed_url.port
        except ValueError as error:
            raise ValueError("Greenhouse URL port is invalid") from error
        if port not in {None, 443}:
            raise ValueError("Greenhouse URL must use the standard HTTPS port")
        if parsed_url.hostname not in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
            raise ValueError("URL is not a supported Greenhouse job board")
        path_parts = tuple(part for part in parsed_url.path.split("/") if part)
        if len(path_parts) < 3 or path_parts[1] != "jobs":
            raise ValueError("Greenhouse URL must contain /{board_token}/jobs/{job_id}")
        board_token, job_id_text = path_parts[0], path_parts[2]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", board_token) or not job_id_text.isdigit():
            raise ValueError("Greenhouse board token or job id is invalid")
        canonical_source_url = (
            f"https://{parsed_url.hostname}/{board_token}/jobs/{int(job_id_text)}"
        )
        return cls(board_token, int(job_id_text), canonical_source_url)

    @property
    def api_url(self) -> str:
        token = quote(self.board_token, safe="")
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{self.job_id}"


@dataclass(frozen=True, slots=True)
class GreenhouseBoardReference:
    board_token: str
    board_url: str

    @classmethod
    def from_url(cls, url: str) -> GreenhouseBoardReference:
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https":
            raise ValueError("Greenhouse board URL must use HTTPS")
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ValueError("Greenhouse board URL must not contain credentials")
        try:
            port = parsed_url.port
        except ValueError as error:
            raise ValueError("Greenhouse board URL port is invalid") from error
        if port not in {None, 443}:
            raise ValueError("Greenhouse board URL must use the standard HTTPS port")
        if parsed_url.hostname not in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
            raise ValueError("URL is not a supported Greenhouse job board")
        path_parts = tuple(part for part in parsed_url.path.split("/") if part)
        if not path_parts or not re.fullmatch(r"[A-Za-z0-9_-]+", path_parts[0]):
            raise ValueError("Greenhouse board token is missing or invalid")
        board_token = path_parts[0]
        return cls(board_token, f"https://{parsed_url.hostname}/{board_token}")

    @property
    def api_url(self) -> str:
        token = quote(self.board_token, safe="")
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseOption(BaseModel):
    label: str


class GreenhouseField(BaseModel):
    name: str
    type: str
    values: list[GreenhouseOption] = Field(default_factory=list)


class GreenhouseQuestion(BaseModel):
    required: bool = False
    label: str
    fields: list[GreenhouseField] = Field(default_factory=list)


class GreenhouseLocation(BaseModel):
    name: str = ""


class GreenhouseJobPayload(BaseModel):
    id: int
    title: str
    company_name: str = ""
    location: GreenhouseLocation = Field(default_factory=GreenhouseLocation)
    content: str = ""
    absolute_url: str
    language: str = "en"
    application_deadline: str | None = None
    questions: list[GreenhouseQuestion] = Field(default_factory=list)
    location_questions: list[GreenhouseQuestion] = Field(default_factory=list)
    compliance: list[GreenhouseQuestion] = Field(default_factory=list)
    data_compliance: list[dict[str, object]] = Field(default_factory=list)
    demographic_questions: dict[str, object] | None = None

    @field_validator(
        "questions",
        "location_questions",
        "compliance",
        "data_compliance",
        mode="before",
    )
    @classmethod
    def normalize_nullable_collections(cls, value: object) -> object:
        return [] if value is None else value


class GreenhouseJobsPayload(BaseModel):
    jobs: list[GreenhouseJobPayload] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GreenhouseSearchHit:
    source_url: str
    title: str
    company: str
    location: str
    description_text: str


@dataclass(frozen=True, slots=True)
class ExtractedGreenhouseJob:
    source_url: str
    job_id: int
    title: str
    company: str
    location: str
    description_text: str
    language: str
    application_deadline: str | None
    form_fields: tuple[FormField, ...]
    requires_sensitive_review: bool
    evidence_api_url: str


GREENHOUSE_FIELD_TYPES: dict[str, FormFieldType | None] = {
    "input_file": FormFieldType.FILE,
    "input_hidden": None,
    "input_text": FormFieldType.TEXT,
    "multi_value_multi_select": FormFieldType.CHECKBOX,
    "multi_value_single_select": FormFieldType.SELECT,
    "textarea": FormFieldType.TEXTAREA,
}


def questions_to_form_fields(
    questions: list[GreenhouseQuestion], *, sensitive: bool = False
) -> tuple[FormField, ...]:
    form_fields: list[FormField] = []
    for question in questions:
        for field in question.fields:
            field_type = GREENHOUSE_FIELD_TYPES.get(field.type, FormFieldType.UNKNOWN)
            if field_type is None:
                continue
            semantic_category, confidence = classify_semantic_category(question.label, field.name)
            if sensitive:
                semantic_category = "protected_demographic"
                confidence = 1.0
            form_fields.append(
                FormField(
                    field_id=field.name,
                    label=question.label,
                    field_type=field_type,
                    is_required=question.required,
                    options=tuple(option.label for option in field.values),
                    semantic_category=semantic_category,
                    confidence=confidence,
                    source_locator=f"greenhouse-api:{field.name}",
                )
            )
    return tuple(form_fields)


class GreenhouseJobBoardApi:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    async def extract_job(self, url: str) -> ExtractedGreenhouseJob:
        reference = GreenhouseJobReference.from_url(url)
        response = await self._http_client.get(
            reference.api_url,
            params={"questions": "true", "pay_transparency": "true"},
        )
        response.raise_for_status()
        payload = GreenhouseJobPayload.model_validate(response.json())
        standard_fields = questions_to_form_fields(
            [*payload.questions, *payload.location_questions]
        )
        compliance_fields = questions_to_form_fields(payload.compliance, sensitive=True)
        requires_sensitive_review = bool(
            compliance_fields or payload.data_compliance or payload.demographic_questions
        )
        return ExtractedGreenhouseJob(
            source_url=reference.source_url,
            job_id=payload.id,
            title=payload.title,
            company=payload.company_name,
            location=payload.location.name,
            description_text=html_to_text(payload.content),
            language=payload.language,
            application_deadline=payload.application_deadline,
            form_fields=(*standard_fields, *compliance_fields),
            requires_sensitive_review=requires_sensitive_review,
            evidence_api_url=reference.api_url,
        )

    async def list_jobs(self, board_url: str) -> tuple[GreenhouseSearchHit, ...]:
        reference = GreenhouseBoardReference.from_url(board_url)
        response = await self._http_client.get(reference.api_url, params={"content": "true"})
        response.raise_for_status()
        payload = GreenhouseJobsPayload.model_validate(response.json())
        return tuple(
            GreenhouseSearchHit(
                source_url=(f"https://boards.greenhouse.io/{reference.board_token}/jobs/{job.id}"),
                title=job.title,
                company=job.company_name or reference.board_token,
                location=job.location.name,
                description_text=html_to_text(job.content),
            )
            for job in payload.jobs
        )
