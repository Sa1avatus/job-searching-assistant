from __future__ import annotations

import hashlib
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.application_email import EmailCategory, outcome_for_category, status_for_category
from app.domain.application_status import TERMINAL_STATUSES
from app.services.application_email_classifier import classify_email_category
from app.services.application_timeline import ApplicationTimelineService
from app.services.email_classification import EmailClassification, EmailClassifier
from app.services.email_entity_extraction import extract_email_entities
from app.services.email_vacancy_matcher import EmailVacancyCandidate, EmailVacancyMatcher
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import ApplicationEmailEventRow, ApplicationRow, UserRow, VacancyRow

logger = structlog.get_logger(__name__)

_MIN_CLASSIFICATION_CONFIDENCE = 0.7
_RAG_MIN_SCORE = 0.25
_RAG_SCORE_MARGIN = 0.05
_REVIEW_STATUS = "needs_review"


@dataclass(frozen=True, slots=True)
class ApplicationEmailEventResult:
    event: ApplicationEmailEventRow
    created: bool
    status_updated: bool


def _decide(
    target_status: str | None,
    confidence: float,
    matched: bool,
) -> tuple[bool, bool]:
    """Return ``(apply_status, needs_review)`` for a classified + matched email."""
    apply = (
        target_status is not None
        and matched
        and confidence >= _MIN_CLASSIFICATION_CONFIDENCE
    )
    needs_review = not apply and (
        target_status is not None or confidence < _MIN_CLASSIFICATION_CONFIDENCE
    )
    return apply, needs_review


class ApplicationEmailEventService:
    def __init__(
        self,
        session: Session,
        timeline: ApplicationTimelineService | None = None,
        *,
        auto_update_enabled: bool = True,
        classifier: EmailClassifier | None = None,
        matcher: EmailVacancyMatcher | None = None,
    ) -> None:
        self._session = session
        self._timeline = timeline or ApplicationTimelineService(session)
        self._auto_update_enabled = auto_update_enabled
        self._classifier = classifier
        self._matcher = matcher

    @property
    def has_llm(self) -> bool:
        return self._classifier is not None

    # ── Deterministic (regex) ingestion — the no-LLM fallback ──────────────

    def ingest(
        self,
        user_id: str,
        subject: str,
        body: str,
        *,
        application_id: str | None = None,
        company: str | None = None,
        vacancy_title: str | None = None,
    ) -> ApplicationEmailEventResult:
        self._require_user(user_id)
        if application_id is None:
            entities = extract_email_entities(subject, body)
            application_id = self.match_application(
                user_id,
                company=company or entities.company,
                vacancy_title=vacancy_title or entities.vacancy_title,
            )
        if application_id is None:
            application_id = self.match_application_text(
                user_id,
                text=f"{subject}\n{body}",
            )
        self._require_owned_application(user_id, application_id)

        classification = _deterministic_classification(subject, body)
        return self._ingest_common(
            user_id,
            subject,
            body,
            application_id=application_id,
            matched=application_id is not None,
            classification=classification,
            candidates=None,
            mark_review_vacancy=False,
        )

    # ── RAG + LLM ingestion ───────────────────────────────────────────────

    async def ingest_async(
        self,
        user_id: str,
        subject: str,
        body: str,
        *,
        application_id: str | None = None,
        company: str | None = None,
        vacancy_title: str | None = None,
    ) -> ApplicationEmailEventResult:
        self._require_user(user_id)
        classification = await self._classify(subject, body)

        if application_id is None:
            application_id, matched, candidates = await self._match(
                user_id,
                subject,
                body,
                company=company or classification.company,
                vacancy_title=vacancy_title or classification.vacancy_title,
            )
        else:
            self._require_owned_application(user_id, application_id)
            matched = True
            candidates = None

        return self._ingest_common(
            user_id,
            subject,
            body,
            application_id=application_id,
            matched=matched,
            classification=classification,
            candidates=candidates,
            mark_review_vacancy=True,
        )

    # ── Shared persistence ────────────────────────────────────────────────

    def _ingest_common(
        self,
        user_id: str,
        subject: str,
        body: str,
        *,
        application_id: str | None,
        matched: bool,
        classification: EmailClassification,
        candidates: list[EmailVacancyCandidate] | None,
        mark_review_vacancy: bool,
    ) -> ApplicationEmailEventResult:
        category = classification.category
        confidence = classification.confidence
        outcome = outcome_for_category(EmailCategory(category)).value
        target_status = status_for_category(EmailCategory(category))
        apply, needs_review = _decide(target_status, confidence, matched)
        candidate_payload = _serialize_candidates(candidates) if candidates else None

        fingerprint = _message_fingerprint(subject, body)
        existing = self._find_by_fingerprint(user_id, fingerprint)

        if existing is not None:
            event_changed = self._refresh_existing(
                existing,
                application_id=application_id,
                category=category,
                confidence=confidence,
                outcome=outcome,
                subject=subject,
                body=body,
                candidates=candidate_payload,
            )
            status_updated = False
            if apply:
                status_updated = self._apply_status(existing, target_status)
            if needs_review and mark_review_vacancy and self._auto_update_enabled:
                status_updated = self._mark_needs_review(existing) or status_updated
            if status_updated:
                existing.needs_review = False
                event_changed = True
            if event_changed or status_updated:
                self._session.commit()
            return ApplicationEmailEventResult(
                event=existing,
                created=False,
                status_updated=status_updated,
            )

        event = ApplicationEmailEventRow(
            user_id=user_id,
            application_id=application_id,
            message_fingerprint=fingerprint,
            outcome=outcome,
            category=category,
            confidence=confidence,
            subject=subject,
            body=body,
            needs_review=needs_review,
            candidates=candidate_payload,
        )
        self._session.add(event)
        if application_id is not None:
            self._timeline.record(
                application_id,
                "email_received",
                new_value=outcome,
                detail={"fingerprint": fingerprint},
                source="email_event",
            )
        status_updated = False
        if apply:
            status_updated = self._apply_status(event, target_status)
        if needs_review and mark_review_vacancy and self._auto_update_enabled:
            status_updated = self._mark_needs_review(event) or status_updated
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            existing = self._find_by_fingerprint(user_id, fingerprint)
            if existing is None:
                raise
            return ApplicationEmailEventResult(
                event=existing,
                created=False,
                status_updated=False,
            )
        return ApplicationEmailEventResult(
            event=event,
            created=True,
            status_updated=status_updated,
        )

    def _refresh_existing(
        self,
        existing: ApplicationEmailEventRow,
        *,
        application_id: str | None,
        category: str,
        confidence: float,
        outcome: str,
        subject: str,
        body: str,
        candidates: list[dict[str, object]] | None,
    ) -> bool:
        """Fill in missing derived fields on an existing event; never re-flag a reviewed one."""
        changed = False
        if existing.application_id is None and application_id is not None:
            existing.application_id = application_id
            changed = True
        if existing.outcome == "unknown" and outcome != "unknown":
            existing.outcome = outcome
            changed = True
        if existing.category is None:
            existing.category = category
            changed = True
        if existing.confidence is None:
            existing.confidence = confidence
            changed = True
        if existing.subject is None and subject:
            existing.subject = subject
            changed = True
        if existing.body is None and body:
            existing.body = body
            changed = True
        if existing.candidates is None and candidates:
            existing.candidates = candidates
            changed = True
        return changed

    # ── Classification and matching ───────────────────────────────────────

    async def _classify(self, subject: str, body: str) -> EmailClassification:
        if self._classifier is None:
            return _deterministic_classification(subject, body)
        try:
            return await self._classifier.classify(subject, body)
        except Exception as error:  # noqa: BLE001 - a failing LLM must not abort email sync
            logger.warning(
                "email_classification_failed",
                error_type=type(error).__name__,
                error=str(error)[:200],
            )
            return _deterministic_classification(subject, body)

    async def _match(
        self,
        user_id: str,
        subject: str,
        body: str,
        *,
        company: str | None,
        vacancy_title: str | None,
    ) -> tuple[str | None, bool, list[EmailVacancyCandidate] | None]:
        """Resolve the application this email belongs to, plus a confidence flag.

        Returns ``(application_id, matched, candidates)`` where ``matched`` is True only when
        the resolution is unambiguous.
        """
        # Strongest signal: exact normalized company/title match.
        entity_id = self.match_application(user_id, company=company, vacancy_title=vacancy_title)
        if entity_id is not None:
            return entity_id, True, None

        # Text substring match (unique title, then unique company).
        text_id = self.match_application_text(user_id, text=f"{subject}\n{body}")
        if text_id is not None:
            return text_id, True, None

        # Semantic retrieval against the vacancies RAG collection.
        candidates: list[EmailVacancyCandidate] = []
        if self._matcher is not None:
            candidates = await self._matcher.match(
                user_id,
                subject,
                body,
                company=company,
                vacancy_title=vacancy_title,
            )
        if candidates:
            top = candidates[0]
            second = candidates[1] if len(candidates) > 1 else None
            clear_winner = second is None or (top.score - second.score) >= _RAG_SCORE_MARGIN
            if top.score >= _RAG_MIN_SCORE and clear_winner:
                return top.application_id, True, None
            return top.application_id, False, candidates

        return None, False, None

    # ── Status application ────────────────────────────────────────────────

    def _apply_status(self, event: ApplicationEmailEventRow, target_status: str | None) -> bool:
        if event.status_applied or event.application_id is None:
            return False
        if not self._auto_update_enabled:
            return False
        if target_status is None:
            return False
        application = self._session.get(ApplicationRow, event.application_id)
        if application is None or application.user_id != event.user_id:
            return False
        previous_status = application.status
        application.status = target_status
        event.status_applied = True
        self._timeline.record_status_change(
            event.application_id,
            previous_status,
            target_status,
            source="email_event",
        )
        return True

    def _mark_needs_review(self, event: ApplicationEmailEventRow) -> bool:
        """Flag the best-guess application as needing human review."""
        if event.application_id is None:
            return False
        application = self._session.get(ApplicationRow, event.application_id)
        if application is None or application.user_id != event.user_id:
            return False
        if application.status in TERMINAL_STATUSES or application.status == _REVIEW_STATUS:
            return False
        previous_status = application.status
        application.status = _REVIEW_STATUS
        self._timeline.record_status_change(
            application.id,
            previous_status,
            _REVIEW_STATUS,
            source="email_event",
        )
        return True

    # ── Matching helpers (unchanged behaviour) ────────────────────────────

    def match_application(
        self,
        user_id: str,
        *,
        company: str | None,
        vacancy_title: str | None,
    ) -> str | None:
        normalized_company = _normalize_company_reference(company)
        normalized_title = _normalize_reference(vacancy_title)
        if normalized_company is None and normalized_title is None:
            return None
        rows = self._session.execute(
            select(ApplicationRow, VacancyRow)
            .join(VacancyRow, VacancyRow.id == ApplicationRow.vacancy_id)
            .where(ApplicationRow.user_id == user_id)
        ).all()
        matches = [
            application.id
            for application, vacancy in rows
            if (
                normalized_company is None
                or _normalize_company_reference(vacancy.company) == normalized_company
            )
            and (
                normalized_title is None or _normalize_reference(vacancy.title) == normalized_title
            )
        ]
        if len(matches) != 1:
            return None
        return str(matches[0])

    def match_application_text(self, user_id: str, *, text: str) -> str | None:
        normalized_text = _normalize_reference(text)
        if normalized_text is None:
            return None
        rows = self._session.execute(
            select(ApplicationRow, VacancyRow)
            .join(VacancyRow, VacancyRow.id == ApplicationRow.vacancy_id)
            .where(ApplicationRow.user_id == user_id)
        ).all()
        title_match_ids: list[str] = []
        company_match_ids: list[str] = []
        for application, vacancy in rows:
            company = _normalize_reference(vacancy.company)
            title = _normalize_reference(vacancy.title)
            title_matches = title is not None and len(title) >= 8 and title in normalized_text
            company_matches = (
                company is not None and len(company) >= 5 and company in normalized_text
            )
            if title_matches:
                title_match_ids.append(application.id)
            if company_matches:
                company_match_ids.append(application.id)
        if len(title_match_ids) == 1:
            return title_match_ids[0]
        if title_match_ids:
            return None
        if len(company_match_ids) == 1:
            return company_match_ids[0]
        return None

    def _require_user(self, user_id: str) -> None:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")

    def _require_owned_application(self, user_id: str, application_id: str | None) -> None:
        if application_id is None:
            return
        application = self._session.get(ApplicationRow, application_id)
        if application is None or application.user_id != user_id:
            raise EntityNotFoundError("Application not found")

    def _find_by_fingerprint(
        self,
        user_id: str,
        fingerprint: str,
    ) -> ApplicationEmailEventRow | None:
        return self._session.scalar(
            select(ApplicationEmailEventRow).where(
                ApplicationEmailEventRow.user_id == user_id,
                ApplicationEmailEventRow.message_fingerprint == fingerprint,
            )
        )

    def list_review_items(
        self,
        user_id: str,
    ) -> list[ApplicationEmailEventRow]:
        return list(
            self._session.scalars(
                select(ApplicationEmailEventRow)
                .where(
                    ApplicationEmailEventRow.user_id == user_id,
                    ApplicationEmailEventRow.needs_review.is_(True),
                )
                .order_by(ApplicationEmailEventRow.processed_at.desc())
            ).all()
        )


def _deterministic_classification(subject: str, body: str) -> EmailClassification:
    category = classify_email_category(subject, body).value
    entities = extract_email_entities(subject, body)
    return EmailClassification(
        category=category,
        # The regex classifier is precise for the categories it has patterns for, and falls
        # through to "other" when nothing matches — that is genuine uncertainty.
        confidence=0.0 if category == "other" else 1.0,
        company=entities.company,
        vacancy_title=entities.vacancy_title,
    )


def _serialize_candidates(
    candidates: list[EmailVacancyCandidate] | None,
) -> list[dict[str, object]] | None:
    if not candidates:
        return None
    return [
        {
            "application_id": candidate.application_id,
            "vacancy_id": candidate.vacancy_id,
            "company": candidate.company,
            "title": candidate.title,
            "score": candidate.score,
            "rag_rank": candidate.rag_rank,
        }
        for candidate in candidates
    ]


def _message_fingerprint(subject: str, body: str) -> str:
    normalized = "\n".join((subject.strip(), body.strip())).encode()
    return hashlib.sha256(normalized).hexdigest()


def _normalize_reference(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).casefold()
    return normalized or None


def _normalize_company_reference(value: str | None) -> str | None:
    normalized = _normalize_reference(value)
    if normalized is None:
        return None
    words = normalized.replace("&", " ").replace(".", " ").split()
    ignored_words = {
        "ag",
        "careers",
        "corp",
        "corporation",
        "gmbh",
        "hr",
        "inc",
        "jobs",
        "limited",
        "llc",
        "ltd",
        "plc",
        "recruiter",
        "recruiting",
        "recruitment",
        "team",
    }
    company_words = [word for word in words if word not in ignored_words]
    if company_words[-2:] == ["talent", "acquisition"]:
        company_words = company_words[:-2]
    return " ".join(company_words) or None
