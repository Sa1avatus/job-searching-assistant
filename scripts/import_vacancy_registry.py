"""Idempotently import vacancy/application rows exported from the user's Google registry.

The Google Drive connector supplies bounded row chunks to this script as base64-encoded JSON.
Running the same chunk again is safe: vacancies are matched by canonical vacancy URL and
applications by the existing (user_id, vacancy_id) uniqueness rule.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.storage.database import SessionFactory
from app.storage.tables import ApplicationRow, UserRow, VacancyRow

SPREADSHEET_ID = "1hSxPagjcfmV4Uokp07VREy9rbUFfGW54ixFCVsjNI1Q"
SHEET_IDS = {"Отклики": 864225540, "LinkedIn": 1765432100}


@dataclass(frozen=True, slots=True)
class RegistryVacancy:
    sheet_name: str
    row_number: int
    company: str
    title: str
    location: str
    source_url: str | None
    platform: str
    original_status: str
    match_score: int
    applied_at: datetime | None
    description_text: str

    @property
    def evidence_url(self) -> str:
        sheet_id = SHEET_IDS[self.sheet_name]
        return (
            f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
            f"#gid={sheet_id}&range=A{self.row_number}"
        )


def canonicalize_vacancy_url(source_url: str | None) -> str | None:
    if not source_url:
        return None
    stripped = source_url.strip()
    linkedin_match = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d+)", stripped)
    if linkedin_match:
        return f"https://www.linkedin.com/jobs/view/{linkedin_match.group(1)}"
    headhunter_match = re.search(r"hh\.ru/vacancy/(\d+)", stripped)
    if headhunter_match:
        return f"https://hh.ru/vacancy/{headhunter_match.group(1)}"
    return stripped


def map_application_status(original_status: str) -> str:
    normalized = original_status.strip().casefold()
    if normalized == "отказ":
        return "rejected"
    if normalized == "собеседование":
        return "interview"
    if normalized == "приём заявок закрыт":
        return "skipped"
    if normalized == "отклик не подтверждён":
        return "saved"
    return "submitted"


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _score(value: object) -> int:
    if isinstance(value, int | float):
        return min(max(round(value), 0), 100)
    return 0


def _excel_date(value: object) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    return (datetime(1899, 12, 30, tzinfo=UTC) + timedelta(days=float(value))).replace(
        microsecond=0
    )


def parse_registry_row(
    sheet_name: str, row_number: int, row: list[object]
) -> RegistryVacancy | None:
    if sheet_name == "Отклики":
        if len(row) < 3 or not _text(row[1]) or not _text(row[2]):
            return None
        values = row + [None] * (18 - len(row))
        direction = _text(values[3])
        platform = _text(values[4])
        resume_note = _text(values[9])
        cover_letter_note = _text(values[10])
        next_step = _text(values[11])
        work_format = _text(values[13])
        notes = _text(values[17])
        description_parts = [
            f"Направление: {direction}" if direction else "",
            f"Формат: {work_format}" if work_format else "",
            f"Резюме: {resume_note}" if resume_note else "",
            f"Сопроводительное письмо: {cover_letter_note}" if cover_letter_note else "",
            f"Следующий шаг: {next_step}" if next_step else "",
            notes,
        ]
        return RegistryVacancy(
            sheet_name=sheet_name,
            row_number=row_number,
            company=_text(values[1]),
            title=_text(values[2]),
            location=_text(values[12]),
            source_url=canonicalize_vacancy_url(_text(values[14]) or None),
            platform=platform,
            original_status=_text(values[6]),
            match_score=_score(values[8]),
            applied_at=_excel_date(values[5]),
            description_text="\n".join(part for part in description_parts if part),
        )
    if sheet_name == "LinkedIn":
        if len(row) < 3 or not _text(row[1]) or not _text(row[2]):
            return None
        values = row + [None] * (12 - len(row))
        job_id = _text(values[3])
        source_url = _text(values[10]) or (
            f"https://www.linkedin.com/jobs/view/{job_id}" if job_id.isdigit() else ""
        )
        application_note = _text(values[8])
        cover_letter_note = _text(values[9])
        notes = _text(values[11])
        description_parts = [
            f"Отклик: {application_note}" if application_note else "",
            f"Сопроводительное письмо: {cover_letter_note}" if cover_letter_note else "",
            notes,
        ]
        return RegistryVacancy(
            sheet_name=sheet_name,
            row_number=row_number,
            company=_text(values[1]),
            title=_text(values[2]),
            location=_text(values[4]),
            source_url=canonicalize_vacancy_url(source_url or None),
            platform="LinkedIn",
            original_status=_text(values[6]),
            match_score=_score(values[5]),
            applied_at=None,
            description_text="\n".join(part for part in description_parts if part),
        )
    raise ValueError(f"Unsupported sheet: {sheet_name}")


def _find_vacancy(session: Session, record: RegistryVacancy) -> VacancyRow | None:
    if record.source_url:
        vacancy = session.scalar(
            select(VacancyRow).where(VacancyRow.source_url == record.source_url)
        )
        if vacancy is not None:
            return vacancy
        source_id_match = re.search(r"/(?:vacancy|jobs/view)/(\d+)", record.source_url)
        if source_id_match:
            vacancy = session.scalar(
                select(VacancyRow).where(VacancyRow.source_url.contains(source_id_match.group(1)))
            )
            if vacancy is not None:
                return vacancy
        synthetic_vacancy = session.scalar(
            select(VacancyRow).where(
                VacancyRow.adapter_name == "google-registry",
                VacancyRow.source_url.startswith(
                    f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/"
                ),
                func.lower(VacancyRow.company) == record.company.casefold(),
                func.lower(VacancyRow.title) == record.title.casefold(),
            )
        )
        if synthetic_vacancy is not None:
            synthetic_vacancy.source_url = record.source_url
            return synthetic_vacancy
        return None
    return session.scalar(
        select(VacancyRow).where(
            func.lower(VacancyRow.company) == record.company.casefold(),
            func.lower(VacancyRow.title) == record.title.casefold(),
        )
    )


def import_chunk(*, user_id: str, payload: dict[str, object]) -> dict[str, int]:
    sheet_name = str(payload["sheet_name"])
    start_row_value = payload["start_row"]
    if not isinstance(start_row_value, int):
        raise ValueError("start_row must be an integer")
    start_row = start_row_value
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    counters = {"rows": 0, "vacancies_created": 0, "applications_created": 0, "existing": 0}
    with SessionFactory() as session:
        if session.get(UserRow, user_id) is None:
            raise ValueError("User not found")
        for offset, raw_row in enumerate(rows):
            if not isinstance(raw_row, list):
                continue
            record = parse_registry_row(sheet_name, start_row + offset, raw_row)
            if record is None:
                continue
            counters["rows"] += 1
            vacancy = _find_vacancy(session, record)
            if vacancy is None:
                vacancy = VacancyRow(
                    source_url=record.source_url or record.evidence_url,
                    title=record.title[:300],
                    company=record.company[:300],
                    location=record.location[:300],
                    description_text=record.description_text,
                    adapter_name="google-registry",
                    source_evidence_url=record.evidence_url,
                    required_skills=[],
                    preferred_skills=[],
                    application_fields=[],
                    requires_sensitive_review=False,
                )
                session.add(vacancy)
                session.flush()
                counters["vacancies_created"] += 1
            application = session.scalar(
                select(ApplicationRow).where(
                    ApplicationRow.user_id == user_id,
                    ApplicationRow.vacancy_id == vacancy.id,
                )
            )
            if application is None:
                warning = f"Импортировано из реестра. Исходный статус: {record.original_status}"
                application = ApplicationRow(
                    user_id=user_id,
                    vacancy_id=vacancy.id,
                    status=map_application_status(record.original_status),
                    match_score=record.match_score,
                    warnings=[warning],
                    cover_letter_text="",
                    created_at=record.applied_at or datetime.now(UTC),
                )
                session.add(application)
                counters["applications_created"] += 1
            else:
                counters["existing"] += 1
        session.commit()
    return counters


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--payload-base64", required=True)
    args = parser.parse_args()
    payload = json.loads(base64.b64decode(args.payload_base64).decode("utf-8"))
    print(json.dumps(import_chunk(user_id=args.user_id, payload=payload), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
