"""Fact ingestion service — file import, LLM extraction, deduplication, batch management.

Supports:
- Structured import from JSON/CSV
- Unstructured import from TXT/MD/PDF/DOCX via LLM extraction
- Resume → Facts extraction
- Deduplication against existing facts
- Import batch tracking with undo capability
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.resume_text import extract_resume_text
from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.matching.normalization import SkillNormalizer
from app.prompts.registry import PromptRegistry
from app.storage.tables import CvFileRow, FactImportBatchRow, ProfileFactRow, UserRow

logger = structlog.get_logger(__name__)

_EXTRACTOR_VERSION = "1"
_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5MB
_MAX_TEXT_CHARS = 48_000
_SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".pdf", ".docx"}


# ── Schemas ──────────────────────────────────────────────────────


class FactSource(StrEnum):
    MANUAL = "manual"
    RESUME = "resume"
    FILE = "file"
    PROJECT = "project"
    INFERRED = "inferred"


class FactStatus(StrEnum):
    ACTIVE = "active"
    EXTRACTED = "extracted"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class DuplicateStatus(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    MERGE_CANDIDATE = "merge_candidate"
    CONFLICT = "conflict"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExtractedFact(StrictModel):
    """A single fact extracted from a file or resume."""

    category: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=5_000)
    skills: list[str] = Field(default_factory=list, max_length=50)
    experience_level: str = Field(default="mentioned", max_length=50)
    source_section: str = Field(default="", max_length=200)
    source_quote: str = Field(default="", max_length=2_000)
    confidence: float = Field(default=0.85, ge=0, le=1)
    experience_started_at: str | None = Field(default=None, max_length=20)
    experience_ended_at: str | None = Field(default=None, max_length=20)

    @field_validator("experience_level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        allowed = {
            "mentioned",
            "used",
            "practical_experience",
            "production_experience",
            "designed",
            "implemented",
            "operated",
            "evaluated",
            "unknown",
        }
        if v not in allowed:
            return "mentioned"
        return v


class ExperienceInterval(StrictModel):
    """A work experience interval extracted from resume."""

    role: str = Field(default="", max_length=300)
    company: str = Field(default="", max_length=300)
    started_at: str = Field(min_length=1, max_length=20)
    ended_at: str | None = Field(default=None, max_length=20)
    domains: list[str] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=50)


class ResumeExtractionResult(StrictModel):
    """Structured output from LLM resume fact extraction."""

    facts: list[ExtractedFact] = Field(default_factory=list, max_length=200)
    experience_intervals: list[ExperienceInterval] = Field(default_factory=list, max_length=50)


class FileExtractionResult(StrictModel):
    """Structured output from LLM file fact extraction."""

    facts: list[ExtractedFact] = Field(default_factory=list, max_length=200)


class StructuredFactInput(StrictModel):
    """Input schema for structured JSON/CSV import."""

    text: str = Field(min_length=1, max_length=5_000)
    type: str = Field(default="project_evidence", max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=50)
    category: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=200)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("category")
    @classmethod
    def derive_category(cls, v: str, info: ValidationInfo) -> str:
        if v:
            return v
        # Derive from type
        type_val = info.data.get("type", "")
        return type_val or "project_evidence"

    @field_validator("name")
    @classmethod
    def derive_name(cls, v: str, info: ValidationInfo) -> str:
        if v:
            return v
        text = info.data.get("text", "")
        return text[:200] if text else "imported_fact"


class CandidateFact(StrictModel):
    """A fact ready for deduplication check and database insert."""

    category: str
    name: str
    value: str
    is_verified: bool = False
    source_type: str = "file"
    source_id: str | None = None
    source_text: str | None = None
    extraction_method: str | None = None
    confidence: float = 0.85
    experience_started_at: str | None = None
    experience_ended_at: str | None = None
    duplicate_status: DuplicateStatus = DuplicateStatus.NEW
    existing_fact_id: str | None = None


class ImportBatchResult(StrictModel):
    """Result of a fact import operation."""

    batch_id: str
    source_type: str
    facts_created: int
    facts_merged: int
    facts_skipped: int
    facts_rejected: int
    candidates: list[CandidateFact] = Field(default_factory=list)


# ── File Parser ──────────────────────────────────────────────────


class FileParser:
    """Parse uploaded files into plain text for LLM extraction."""

    def parse(self, filename: str, content: bytes) -> str:
        ext = _extension(filename)
        if ext in {".txt", ".md"}:
            return self._decode_text(content)
        if ext == ".csv":
            return self._parse_csv(content)
        if ext == ".json":
            return self._parse_json_as_text(content)
        if ext == ".pdf":
            return extract_resume_text(content, extension=".pdf")
        if ext == ".docx":
            return extract_resume_text(content, extension=".docx")
        raise ValueError(f"Unsupported file type: {ext}")

    def parse_structured(self, filename: str, content: bytes) -> list[StructuredFactInput]:
        ext = _extension(filename)
        if ext == ".json":
            return self._parse_json_structured(content)
        if ext == ".csv":
            return self._parse_csv_structured(content)
        raise ValueError(f"Structured parse not supported for {ext}")

    def _decode_text(self, content: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-16", "cp1251", "latin-1"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not determine text encoding")

    def _parse_csv(self, content: bytes) -> str:
        text = self._decode_text(content)
        reader = csv.reader(io.StringIO(text))
        lines = []
        for row in reader:
            lines.append(" | ".join(row))
        return "\n".join(lines)

    def _parse_json_as_text(self, content: bytes) -> str:
        text = self._decode_text(content)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(data, list):
            parts = []
            for item in data:
                if isinstance(item, dict):
                    parts.append(json.dumps(item, ensure_ascii=False))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _parse_json_structured(self, content: bytes) -> list[StructuredFactInput]:
        text = self._decode_text(content)
        data = json.loads(text)
        if not isinstance(data, list):
            data = [data]
        results = []
        for item in data:
            if isinstance(item, dict):
                results.append(StructuredFactInput.model_validate(item))
        return results

    def _parse_csv_structured(self, content: bytes) -> list[StructuredFactInput]:
        text = self._decode_text(content)
        reader = csv.DictReader(io.StringIO(text))
        results = []
        for row in reader:
            fact_dict: dict[str, Any] = {
                "text": row.get("text", row.get("value", "")),
                "type": row.get("type", row.get("category", "project_evidence")),
                "category": row.get("category", ""),
                "name": row.get("name", ""),
                "confidence": float(row.get("confidence", "1.0")),
            }
            skills_str = row.get("skills", "")
            if skills_str:
                fact_dict["skills"] = [s.strip() for s in skills_str.split(",") if s.strip()]
            else:
                fact_dict["skills"] = []
            results.append(StructuredFactInput.model_validate(fact_dict))
        return results


# ── LLM Fact Extractor ──────────────────────────────────────────


class LLMFactExtractor:
    """Extract structured facts from text using LLM."""

    def __init__(
        self,
        router: ModelRouter,
        prompt_registry: PromptRegistry,
        *,
        skill_normalizer: SkillNormalizer | None = None,
    ) -> None:
        self._router = router
        self._prompt_registry = prompt_registry
        self._skill_normalizer = skill_normalizer or SkillNormalizer()

    async def extract_from_text(
        self, text: str, *, source_type: str = "file"
    ) -> FileExtractionResult:
        truncated = self._truncate(text)
        prompt = self._prompt_registry.render(
            "extract_facts_from_text",
            {"source_type": source_type, "source_text": truncated},
        )
        result = await self._router.route(
            ModelRequest(
                task_name="extract_facts_from_text",
                task_class=ModelTaskClass.LOW_COST,
                prompt=prompt,
                max_cost_usd=0.05,
                timeout_seconds=90,
            ),
            FileExtractionResult,
        )
        result = self._normalize_facts(result)
        return result

    async def extract_from_resume(
        self, resume_text: str, *, resume_id: str
    ) -> ResumeExtractionResult:
        truncated = self._truncate(resume_text)
        prompt = self._prompt_registry.render(
            "extract_facts_from_resume",
            {"resume_id": resume_id, "resume_text": truncated},
        )
        result = await self._router.route(
            ModelRequest(
                task_name="extract_facts_from_resume",
                task_class=ModelTaskClass.LOW_COST,
                prompt=prompt,
                max_cost_usd=0.08,
                timeout_seconds=120,
            ),
            ResumeExtractionResult,
        )
        result = self._normalize_resume_result(result)
        return result

    def _normalize_facts(self, result: FileExtractionResult) -> FileExtractionResult:
        normalized = []
        for fact in result.facts:
            norm_skills = []
            for skill in fact.skills:
                ns = self._skill_normalizer.normalize(skill)
                norm_skills.append(ns.canonical)
            normalized.append(fact.model_copy(update={"skills": norm_skills}))
        return result.model_copy(update={"facts": normalized})

    def _normalize_resume_result(self, result: ResumeExtractionResult) -> ResumeExtractionResult:
        normalized_facts = []
        for fact in result.facts:
            norm_skills = []
            for skill in fact.skills:
                ns = self._skill_normalizer.normalize(skill)
                norm_skills.append(ns.canonical)
            normalized_facts.append(fact.model_copy(update={"skills": norm_skills}))
        return result.model_copy(update={"facts": normalized_facts})

    @staticmethod
    def _truncate(text: str) -> str:
        if len(text) <= _MAX_TEXT_CHARS:
            return text
        half = _MAX_TEXT_CHARS // 2
        return text[:half] + "\n\n[...omitted...]\n\n" + text[-half:]


# ── Deduplication ────────────────────────────────────────────────


class FactDeduplicator:
    """Deduplicate candidate facts against existing facts in the database."""

    def __init__(
        self, session: Session, *, skill_normalizer: SkillNormalizer | None = None
    ) -> None:
        self._session = session
        self._skill_normalizer = skill_normalizer or SkillNormalizer()

    def check_duplicates(
        self, user_id: str, candidates: list[CandidateFact]
    ) -> list[CandidateFact]:
        existing = self._session.scalars(
            select(ProfileFactRow).where(
                ProfileFactRow.user_id == user_id,
                ProfileFactRow.status.in_(["active", "verified", "extracted"]),
            )
        ).all()
        existing_by_key: dict[str, ProfileFactRow] = {}
        for fact in existing:
            key = self._fact_key(fact.category, fact.name)
            existing_by_key[key] = fact

        result = []
        for candidate in candidates:
            key = self._fact_key(candidate.category, candidate.name)
            if key in existing_by_key:
                existing_fact = existing_by_key[key]
                if self._is_merge_candidate(existing_fact, candidate):
                    candidate.duplicate_status = DuplicateStatus.MERGE_CANDIDATE
                    candidate.existing_fact_id = existing_fact.id
                else:
                    candidate.duplicate_status = DuplicateStatus.DUPLICATE
                    candidate.existing_fact_id = existing_fact.id
            else:
                # Check near-duplicate by normalized name
                near = self._find_near_duplicate(candidate, existing)
                if near is not None:
                    candidate.duplicate_status = DuplicateStatus.MERGE_CANDIDATE
                    candidate.existing_fact_id = near.id
                else:
                    candidate.duplicate_status = DuplicateStatus.NEW
            result.append(candidate)
        return result

    def _fact_key(self, category: str, name: str) -> str:
        return f"{category.casefold().strip()}:{name.casefold().strip()}"

    def _is_merge_candidate(self, existing: ProfileFactRow, candidate: CandidateFact) -> bool:
        """New fact has more information than existing."""
        existing_len = len(existing.value or "")
        candidate_len = len(candidate.value or "")
        return candidate_len > existing_len * 1.5

    def _find_near_duplicate(
        self, candidate: CandidateFact, existing: Sequence[ProfileFactRow]
    ) -> ProfileFactRow | None:
        """Find near-duplicate by normalized name overlap."""
        candidate_tokens = set(candidate.name.casefold().split())
        # Check by name and value overlap
        for fact in existing:
            existing_tokens = set(fact.name.casefold().split())
            if not candidate_tokens or not existing_tokens:
                continue
            overlap = len(candidate_tokens & existing_tokens) / max(len(candidate_tokens), 1)
            if overlap >= 0.6:
                return fact
        return None


# ── Import Service ───────────────────────────────────────────────


class FactIngestionService:
    """Orchestrates fact import from files and resumes."""

    def __init__(
        self,
        session: Session,
        router: ModelRouter,
        prompt_registry: PromptRegistry,
    ) -> None:
        self._session = session
        self._router = router
        self._prompt_registry = prompt_registry
        self._parser = FileParser()
        self._extractor = LLMFactExtractor(router, prompt_registry)
        self._deduplicator = FactDeduplicator(session)

    async def import_from_file(
        self,
        user_id: str,
        filename: str,
        content: bytes,
        *,
        auto_accept: bool = False,
    ) -> ImportBatchResult:
        """Import facts from an uploaded file."""
        self._require_user(user_id)
        ext = _extension(filename)
        if ext not in _SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {ext}")
        if len(content) > _MAX_FILE_BYTES:
            raise ValueError("File too large")

        # Create batch
        batch = FactImportBatchRow(
            user_id=user_id,
            source_type="file",
            source_filename=filename,
            extractor_version=_EXTRACTOR_VERSION,
            status="processing",
        )
        self._session.add(batch)
        self._session.flush()

        try:
            if ext in {".json", ".csv"}:
                candidates = await self._import_structured(user_id, filename, content, batch)
            else:
                candidates = await self._import_unstructured(user_id, filename, content, batch)

            # Deduplicate
            candidates = self._deduplicator.check_duplicates(user_id, candidates)

            # Save
            created, merged, skipped, rejected = self._save_candidates(
                user_id, candidates, batch.id, auto_accept
            )

            batch.facts_created = created
            batch.facts_merged = merged
            batch.facts_skipped = skipped
            batch.facts_rejected = rejected
            batch.status = "completed"
            self._session.commit()

            logger.info(
                "fact_import_completed",
                batch_id=batch.id,
                user_id=user_id,
                source_type="file",
                created=created,
                merged=merged,
                skipped=skipped,
            )

            return ImportBatchResult(
                batch_id=batch.id,
                source_type="file",
                facts_created=created,
                facts_merged=merged,
                facts_skipped=skipped,
                facts_rejected=rejected,
                candidates=candidates,
            )
        except Exception as error:
            batch.status = "failed"
            self._session.commit()
            logger.warning(
                "fact_import_failed",
                batch_id=batch.id,
                error_type=type(error).__name__,
            )
            raise

    async def extract_from_resume(
        self,
        user_id: str,
        cv_file_id: str,
        *,
        force: bool = False,
        auto_accept: bool = False,
    ) -> ImportBatchResult:
        """Extract facts from an existing resume."""
        self._require_user(user_id)
        cv_file = self._session.get(CvFileRow, cv_file_id)
        if cv_file is None or cv_file.user_id != user_id:
            raise ValueError("Resume not found")

        # Check if already extracted
        content_hash = cv_file.sha256
        if not force:
            existing_batch = self._session.scalar(
                select(FactImportBatchRow).where(
                    FactImportBatchRow.user_id == user_id,
                    FactImportBatchRow.source_type == "resume",
                    FactImportBatchRow.source_id == content_hash,
                    FactImportBatchRow.extractor_version == _EXTRACTOR_VERSION,
                    FactImportBatchRow.status == "completed",
                )
            )
            if existing_batch is not None:
                logger.info(
                    "resume_already_extracted",
                    batch_id=existing_batch.id,
                    cv_file_id=cv_file_id,
                )
                return ImportBatchResult(
                    batch_id=existing_batch.id,
                    source_type="resume",
                    facts_created=existing_batch.facts_created,
                    facts_merged=existing_batch.facts_merged,
                    facts_skipped=existing_batch.facts_skipped,
                    facts_rejected=existing_batch.facts_rejected,
                    candidates=[],
                )

        # Extract resume text
        from pathlib import Path

        cv_path = Path(cv_file.storage_path)
        resume_bytes = cv_path.read_bytes()
        resume_text = extract_resume_text(
            resume_bytes, extension=Path(cv_file.original_filename).suffix
        )

        # Create batch
        batch = FactImportBatchRow(
            user_id=user_id,
            source_type="resume",
            source_id=content_hash,
            source_filename=cv_file.original_filename,
            extractor_version=_EXTRACTOR_VERSION,
            status="processing",
        )
        self._session.add(batch)
        self._session.flush()

        try:
            # LLM extraction
            extraction = await self._extractor.extract_from_resume(
                resume_text, resume_id=cv_file_id
            )

            # Convert to candidates
            candidates = []
            for fact in extraction.facts:
                candidates.append(
                    CandidateFact(
                        category=fact.category,
                        name=fact.name,
                        value=fact.value,
                        is_verified=False,
                        source_type="resume",
                        source_id=cv_file_id,
                        source_text=fact.source_quote or fact.value,
                        extraction_method="llm",
                        confidence=fact.confidence,
                        experience_started_at=fact.experience_started_at,
                        experience_ended_at=fact.experience_ended_at,
                    )
                )

            # Add experience intervals as facts
            for interval in extraction.experience_intervals:
                interval_text = f"{interval.role}"
                if interval.company:
                    interval_text += f" at {interval.company}"
                interval_text += f" ({interval.started_at} — {interval.ended_at or 'present'})"
                candidates.append(
                    CandidateFact(
                        category="work_experience",
                        name=f"{interval.role} ({interval.started_at})",
                        value=interval_text,
                        is_verified=False,
                        source_type="resume",
                        source_id=cv_file_id,
                        source_text=interval_text,
                        extraction_method="llm",
                        confidence=0.9,
                        experience_started_at=interval.started_at,
                        experience_ended_at=interval.ended_at,
                    )
                )

            # Deduplicate
            candidates = self._deduplicator.check_duplicates(user_id, candidates)

            # Save
            created, merged, skipped, rejected = self._save_candidates(
                user_id, candidates, batch.id, auto_accept
            )

            batch.facts_created = created
            batch.facts_merged = merged
            batch.facts_skipped = skipped
            batch.facts_rejected = rejected
            batch.status = "completed"
            self._session.commit()

            # Invalidate matching cache for this user
            self._invalidate_matching(user_id)

            logger.info(
                "resume_extraction_completed",
                batch_id=batch.id,
                user_id=user_id,
                cv_file_id=cv_file_id,
                created=created,
                merged=merged,
                skipped=skipped,
            )

            return ImportBatchResult(
                batch_id=batch.id,
                source_type="resume",
                facts_created=created,
                facts_merged=merged,
                facts_skipped=skipped,
                facts_rejected=rejected,
                candidates=candidates,
            )
        except Exception as error:
            batch.status = "failed"
            self._session.commit()
            logger.warning(
                "resume_extraction_failed",
                batch_id=batch.id,
                error_type=type(error).__name__,
            )
            raise

    async def _import_structured(
        self,
        user_id: str,
        filename: str,
        content: bytes,
        batch: FactImportBatchRow,
    ) -> list[CandidateFact]:
        """Import from JSON/CSV structured format."""
        structured = self._parser.parse_structured(filename, content)
        candidates = []
        for item in structured:
            candidates.append(
                CandidateFact(
                    category=item.category or item.type,
                    name=item.name or item.text[:200],
                    value=item.text,
                    is_verified=False,
                    source_type="file",
                    source_id=filename,
                    source_text=item.text,
                    extraction_method="structured_import",
                    confidence=item.confidence,
                )
            )
        return candidates

    async def _import_unstructured(
        self,
        user_id: str,
        filename: str,
        content: bytes,
        batch: FactImportBatchRow,
    ) -> list[CandidateFact]:
        """Import from TXT/MD/PDF/DOCX via LLM extraction."""
        text = self._parser.parse(filename, content)
        extraction = await self._extractor.extract_from_text(text, source_type="file")
        candidates = []
        for fact in extraction.facts:
            candidates.append(
                CandidateFact(
                    category=fact.category,
                    name=fact.name,
                    value=fact.value,
                    is_verified=False,
                    source_type="file",
                    source_id=filename,
                    source_text=fact.source_quote or fact.value,
                    extraction_method="llm",
                    confidence=fact.confidence,
                )
            )
        return candidates

    def _save_candidates(
        self,
        user_id: str,
        candidates: list[CandidateFact],
        batch_id: str,
        auto_accept: bool,
    ) -> tuple[int, int, int, int]:
        """Save candidates to database. Returns (created, merged, skipped, rejected)."""
        created = 0
        merged = 0
        skipped = 0
        rejected = 0

        for candidate in candidates:
            if candidate.duplicate_status == DuplicateStatus.DUPLICATE:
                skipped += 1
                continue

            if candidate.duplicate_status == DuplicateStatus.MERGE_CANDIDATE:
                if candidate.existing_fact_id and auto_accept:
                    # Merge: update existing fact with richer information
                    existing = self._session.get(ProfileFactRow, candidate.existing_fact_id)
                    if existing is not None and existing.user_id == user_id:
                        existing.value = candidate.value
                        existing.confidence = max(existing.confidence, candidate.confidence)
                        existing.source_text = candidate.source_text
                        existing.extraction_method = candidate.extraction_method
                        merged += 1
                        continue
                skipped += 1
                continue

            # NEW fact
            status = "active" if auto_accept else "extracted"
            fact = ProfileFactRow(
                user_id=user_id,
                category=candidate.category,
                name=candidate.name,
                value=candidate.value,
                is_verified=auto_accept,
                source_type=candidate.source_type,
                source_id=candidate.source_id,
                source_text=candidate.source_text,
                extraction_method=candidate.extraction_method,
                batch_id=batch_id,
                confidence=candidate.confidence,
                experience_started_at=candidate.experience_started_at,
                experience_ended_at=candidate.experience_ended_at,
                status=status,
            )
            self._session.add(fact)
            created += 1

        return created, merged, skipped, rejected

    def _invalidate_matching(self, user_id: str) -> None:
        """Mark matching results as stale after fact changes."""
        from app.storage.tables import ApplicationMatchResultRow, ApplicationRow

        applications = self._session.scalars(
            select(ApplicationRow).where(ApplicationRow.user_id == user_id)
        ).all()
        for app in applications:
            match_result = self._session.get(ApplicationMatchResultRow, app.id)
            if match_result is not None and match_result.status == "scored":
                match_result.status = "stale"
                match_result.fallback_reason = (
                    "Facts changed since last calculation; re-score recommended"
                )

    def undo_batch(self, user_id: str, batch_id: str) -> int:
        """Undo an import batch: remove facts that weren't manually modified."""
        batch = self._session.get(FactImportBatchRow, batch_id)
        if batch is None or batch.user_id != user_id:
            raise ValueError("Batch not found")

        facts = self._session.scalars(
            select(ProfileFactRow).where(
                ProfileFactRow.batch_id == batch_id,
                ProfileFactRow.user_id == user_id,
            )
        ).all()

        removed = 0
        for fact in facts:
            if fact.is_verified and fact.source_type != "manual":
                # Fact was verified after import — skip with warning
                logger.info(
                    "undo_skip_verified_fact",
                    fact_id=fact.id,
                    batch_id=batch_id,
                )
                continue
            fact.status = "superseded"
            removed += 1

        batch.status = "undone"
        self._session.commit()
        self._invalidate_matching(user_id)
        return removed

    def list_batches(self, user_id: str) -> list[FactImportBatchRow]:
        """List import batches for a user."""
        return list(
            self._session.scalars(
                select(FactImportBatchRow)
                .where(FactImportBatchRow.user_id == user_id)
                .order_by(FactImportBatchRow.created_at.desc())
            ).all()
        )

    def _require_user(self, user_id: str) -> None:
        if self._session.get(UserRow, user_id) is None:
            raise ValueError("User not found")


def _extension(filename: str) -> str:
    from pathlib import Path

    return Path(filename).suffix.casefold()
