from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from app.domain.forms import FormField, FormFieldType


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if text := data.strip():
            self.parts.append(text)


def html_to_text(content: str) -> str:
    parser = _TextExtractor()
    parser.feed(content)
    return "\n".join(parser.parts)


@dataclass(frozen=True, slots=True)
class HeadHunterVacancyReference:
    vacancy_id: str
    source_url: str

    @classmethod
    def from_url(cls, url: str) -> HeadHunterVacancyReference:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or not (hostname == "hh.ru" or hostname.endswith(".hh.ru")):
            raise ValueError("URL is not a supported hh.ru vacancy")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("hh.ru URL must not contain credentials")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("hh.ru URL port is invalid") from error
        if port not in {None, 443}:
            raise ValueError("hh.ru URL must use the standard HTTPS port")
        match = re.fullmatch(r"/vacancy/(\d+)/?", parsed.path)
        if match is None:
            raise ValueError("hh.ru URL must contain /vacancy/{numeric_id}")
        vacancy_id = match.group(1)
        return cls(vacancy_id, f"https://hh.ru/vacancy/{vacancy_id}")

    @property
    def api_url(self) -> str:
        return f"https://api.hh.ru/vacancies/{self.vacancy_id}"


class HeadHunterNamedValue(BaseModel):
    name: str = ""


class HeadHunterVacancyPayload(BaseModel):
    id: str
    name: str
    description: str = ""
    alternate_url: str
    employer: HeadHunterNamedValue = Field(default_factory=HeadHunterNamedValue)
    area: HeadHunterNamedValue = Field(default_factory=HeadHunterNamedValue)
    key_skills: list[HeadHunterNamedValue] = Field(default_factory=list)
    response_letter_required: bool = False
    has_test: bool = False
    apply_alternate_url: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedHeadHunterVacancy:
    source_url: str
    title: str
    company: str
    location: str
    description_text: str
    required_skills: tuple[str, ...]
    form_fields: tuple[FormField, ...]
    evidence_api_url: str
    requires_sensitive_review: bool


@dataclass(frozen=True, slots=True)
class HeadHunterSearchHit:
    vacancy_id: str
    source_url: str
    title: str
    company: str


class HeadHunterApi:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        user_agent: str,
        access_token: str | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("HeadHunter API requires a non-empty User-Agent")
        self._http_client = http_client
        self._user_agent = user_agent
        self._access_token = access_token

    async def extract_vacancy(self, url: str) -> ExtractedHeadHunterVacancy:
        reference = HeadHunterVacancyReference.from_url(url)
        headers = {"User-Agent": self._user_agent}
        if self._access_token is not None:
            headers["Authorization"] = f"Bearer {self._access_token}"
        response = await self._http_client.get(reference.api_url, headers=headers)
        response.raise_for_status()
        payload = HeadHunterVacancyPayload.model_validate(response.json())
        if payload.id != reference.vacancy_id:
            raise ValueError("HeadHunter response vacancy id does not match the requested id")
        fields = [
            FormField(
                "resume",
                "Резюме",
                FormFieldType.FILE,
                True,
                semantic_category="resume",
                confidence=1.0,
                source_locator="headhunter-api:resume",
            )
        ]
        if payload.response_letter_required:
            fields.append(
                FormField(
                    "cover_letter",
                    "Сопроводительное письмо",
                    FormFieldType.TEXTAREA,
                    True,
                    semantic_category="cover_letter",
                    confidence=1.0,
                    source_locator="headhunter-api:response_letter_required",
                )
            )
        return ExtractedHeadHunterVacancy(
            source_url=reference.source_url,
            title=payload.name,
            company=payload.employer.name,
            location=payload.area.name,
            description_text=html_to_text(payload.description),
            required_skills=tuple(skill.name for skill in payload.key_skills if skill.name),
            form_fields=tuple(fields),
            evidence_api_url=reference.api_url,
            requires_sensitive_review=payload.has_test,
        )

    async def resolve_area_ids(self, location_names: list[str]) -> list[str]:
        """Resolve free-text location names (e.g. "Москва") to hh.ru numeric area ids.

        An empty list, or any name in the "anywhere" set, means "no location filter" — the
        caller should search nationwide/worldwide rather than pass an empty area list that hh.ru
        would otherwise interpret differently.
        """
        normalized = {name.strip().casefold() for name in location_names if name.strip()}
        anywhere_markers = {"anywhere", "везде", "any", "любое", "remote", "удалённо", "удаленно"}
        if not normalized or normalized & anywhere_markers:
            return []
        headers = {"User-Agent": self._user_agent}
        response = await self._http_client.get("https://api.hh.ru/areas", headers=headers)
        response.raise_for_status()
        matched_ids: list[str] = []

        def walk(nodes: list[dict[str, object]]) -> None:
            for node in nodes:
                name = str(node.get("name", "")).strip().casefold()
                if name in normalized:
                    matched_ids.append(str(node.get("id")))
                children = node.get("areas")
                if isinstance(children, list):
                    walk(children)

        walk(response.json())
        return matched_ids

    async def search_vacancies(
        self, *, text: str, area_ids: list[str], limit: int = 10
    ) -> list[HeadHunterSearchHit]:
        headers = {"User-Agent": self._user_agent}
        if self._access_token is not None:
            headers["Authorization"] = f"Bearer {self._access_token}"
        params: dict[str, str | list[str]] = {
            "text": text,
            "per_page": str(min(max(limit, 1), 100)),
            "area": area_ids,
        }
        response = await self._http_client.get(
            "https://api.hh.ru/vacancies", headers=headers, params=params
        )
        response.raise_for_status()
        payload = response.json()
        hits: list[HeadHunterSearchHit] = []
        for item in (payload.get("items") or [])[:limit]:
            vacancy_id = str(item.get("id") or "").strip()
            if not vacancy_id:
                continue
            employer = item.get("employer") or {}
            hits.append(
                HeadHunterSearchHit(
                    vacancy_id=vacancy_id,
                    source_url=f"https://hh.ru/vacancy/{vacancy_id}",
                    title=str(item.get("name") or ""),
                    company=str(employer.get("name") or ""),
                )
            )
        return hits
