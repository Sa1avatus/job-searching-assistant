"""Application CRM: one journey per application and owner-scoped funnel analytics.

Everything is derived from *events* (status changes, received emails, the submission ledger),
never from a model score: the score only appears as a grouping dimension so a reader can see
whether high-scored applications actually got answered, without the CRM claiming that the score
caused anything. ``manual_update`` timeline rows (human matching feedback) are ignored - they are
labels for the ranker, not outcomes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.crm_stats import (
    LOW_SAMPLE,
    MATURITY_DAYS,
    RateStat,
    distinguishable,
    rate,
    score_band,
)
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    ApplicationSubmissionRow,
    ApplicationTimelineEventRow,
    CvFileRow,
    UserRow,
    VacancyRow,
)

GROUP_BY = ("source", "company", "resume", "role", "score_band", "strategy")
_IMPLIES_SENT = frozenset({"submitted", "interview", "offer", "employer_rejected"})
_RESPONSE_STATUSES = frozenset({"interview", "offer", "employer_rejected"})
_EVENT_SOURCE_LABEL = {"status_change": "status", "email_received": "email"}


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _normalise(value: str | None) -> str:
    return re.sub(r"[\W_]+", " ", (value or "").casefold()).strip() or "unknown"


@dataclass(slots=True)
class ApplicationFacts:
    application_id: str
    vacancy_id: str
    title: str
    company: str
    source: str
    resume_id: str | None
    resume_label: str
    score_band: str
    strategy: str
    status: str
    created_at: datetime
    submitted: bool = False
    submitted_at: datetime | None = None
    responded: bool = False
    responded_at: datetime | None = None
    interview: bool = False
    interview_at: datetime | None = None
    offer: bool = False
    offer_at: datetime | None = None
    rejected: bool = False
    rejected_at: datetime | None = None
    evidence: dict[str, str] = field(default_factory=dict)

    def mature(self, now: datetime) -> bool:
        """Old enough to have been answered, or already answered."""
        if self.responded:
            return True
        if self.submitted_at is None:
            return self.submitted  # legacy row without a timestamp: cannot be aged, count it
        return self.submitted_at <= now - timedelta(days=MATURITY_DAYS)


def _earliest(current: datetime | None, candidate: datetime | None) -> datetime | None:
    if candidate is None:
        return current
    return candidate if current is None or candidate < current else current


class ApplicationCrmService:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ── facts ────────────────────────────────────────────────────────────────

    def facts(self, user_id: str) -> list[ApplicationFacts]:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")
        rows = self._session.execute(
            select(ApplicationRow, VacancyRow)
            .join(VacancyRow, VacancyRow.id == ApplicationRow.vacancy_id)
            .where(ApplicationRow.user_id == user_id)
            .order_by(ApplicationRow.created_at, ApplicationRow.id)
        ).all()
        if not rows:
            return []
        ids = [application.id for application, _ in rows]
        resumes = {
            cv_id: name
            for cv_id, name in self._session.execute(
                select(CvFileRow.id, CvFileRow.original_filename).where(
                    CvFileRow.user_id == user_id
                )
            )
        }
        strategies = {
            application_id: version
            for application_id, version in self._session.execute(
                select(
                    ApplicationMatchResultRow.application_id,
                    ApplicationMatchResultRow.scoring_version,
                ).where(ApplicationMatchResultRow.application_id.in_(ids))
            )
        }
        events: dict[str, list[ApplicationTimelineEventRow]] = defaultdict(list)
        for event in self._session.scalars(
            select(ApplicationTimelineEventRow)
            .where(
                ApplicationTimelineEventRow.application_id.in_(ids),
                ApplicationTimelineEventRow.event_type.in_(tuple(_EVENT_SOURCE_LABEL)),
            )
            .order_by(ApplicationTimelineEventRow.occurred_at, ApplicationTimelineEventRow.id)
        ):
            events[event.application_id].append(event)
        confirmed = {
            application_id: created
            for application_id, created in self._session.execute(
                select(
                    ApplicationSubmissionRow.application_id, ApplicationSubmissionRow.created_at
                ).where(
                    ApplicationSubmissionRow.application_id.in_(ids),
                    ApplicationSubmissionRow.state == "confirmed",
                    ApplicationSubmissionRow.verified_by.is_not(None),
                    ApplicationSubmissionRow.verified_by != "legacy",
                )
            )
        }
        return [
            self._build(application, vacancy, resumes, strategies, events, confirmed)
            for application, vacancy in rows
        ]

    def _build(
        self,
        application: ApplicationRow,
        vacancy: VacancyRow,
        resumes: dict[str, str],
        strategies: dict[str, str],
        events: dict[str, list[ApplicationTimelineEventRow]],
        confirmed: dict[str, datetime],
    ) -> ApplicationFacts:
        facts = ApplicationFacts(
            application_id=application.id,
            vacancy_id=vacancy.id,
            title=vacancy.title,
            company=vacancy.company,
            source=vacancy.source_key or "other",
            resume_id=application.selected_cv_file_id,
            resume_label=resumes.get(application.selected_cv_file_id or "", "no resume selected"),
            score_band=score_band(application.match_score),
            strategy=strategies.get(application.id, "none"),
            status=application.status,
            created_at=_aware(application.created_at) or datetime.now(UTC),
        )
        reached: dict[str, datetime | None] = {}
        for event in events.get(application.id, []):
            at = _aware(event.occurred_at)
            if event.event_type == "email_received":
                if not facts.responded or (
                    at and (facts.responded_at is None or at < facts.responded_at)
                ):
                    facts.responded = True
                    facts.responded_at = _earliest(facts.responded_at, at)
                    facts.evidence.setdefault("responded", "an employer email was received")
            elif event.new_value:
                reached[event.new_value] = _earliest(reached.get(event.new_value), at)
        # the current status counts as reached even when no event recorded it (legacy data)
        reached.setdefault(application.status, None)

        implied = [(status, at) for status, at in reached.items() if status in _IMPLIES_SENT]
        if implied:
            facts.submitted = True
            timestamps = [at for status, at in implied if status == "submitted" and at is not None]
            facts.submitted_at = min(timestamps) if timestamps else None
            facts.evidence["submitted"] = "status reached: " + ", ".join(
                sorted(s for s, _ in implied)
            )
        if application.id in confirmed:
            facts.submitted = True
            facts.submitted_at = _earliest(facts.submitted_at, _aware(confirmed[application.id]))
            facts.evidence.setdefault("submitted", "the submission ledger confirms it was sent")

        for status, attribute in (
            ("interview", "interview"),
            ("offer", "offer"),
            ("employer_rejected", "rejected"),
        ):
            if status in reached:
                setattr(facts, attribute, True)
                setattr(facts, f"{attribute}_at", reached[status])
                facts.evidence.setdefault(attribute, f"status reached: {status}")
        if any(status in _RESPONSE_STATUSES for status in reached):
            facts.responded = True
            candidates = [reached[s] for s in _RESPONSE_STATUSES if s in reached]
            for at in candidates:
                facts.responded_at = _earliest(facts.responded_at, at)
            facts.evidence.setdefault("responded", "the employer's decision status was recorded")
        return facts

    # ── journey ─────────────────────────────────────────────────────────────

    def journey(self, user_id: str, application_id: str) -> dict[str, Any]:
        application = self._session.get(ApplicationRow, application_id)
        if application is None or application.user_id != user_id:
            raise EntityNotFoundError("Application not found")
        facts = next(f for f in self.facts(user_id) if f.application_id == application_id)

        def stage(
            name: str, reached: bool, at: datetime | None, evidence: str = ""
        ) -> dict[str, Any]:
            return {
                "stage": name,
                "reached": reached,
                "at": at.isoformat() if at else None,
                "evidence": evidence,
            }

        stages = [
            stage("saved", True, facts.created_at, "the vacancy was saved as an application"),
            stage(
                "submitted",
                facts.submitted,
                facts.submitted_at,
                facts.evidence.get("submitted", ""),
            ),
            stage(
                "responded",
                facts.responded,
                facts.responded_at,
                facts.evidence.get("responded", ""),
            ),
            stage(
                "interview",
                facts.interview,
                facts.interview_at,
                facts.evidence.get("interview", ""),
            ),
            stage("offer", facts.offer, facts.offer_at, facts.evidence.get("offer", "")),
        ]
        outcome = (
            "offer"
            if facts.offer
            else "employer_rejected"
            if facts.rejected
            else facts.status
            if facts.status in {"withdrawn", "rejected", "skipped"}
            else "open"
        )
        return {
            "application_id": application_id,
            "vacancy_id": facts.vacancy_id,
            "title": facts.title,
            "company": facts.company,
            "source": facts.source,
            "current_status": facts.status,
            "outcome": outcome,
            "stages": stages,
        }

    # ── analytics ───────────────────────────────────────────────────────────

    def funnel(
        self, user_id: str, group_by: str = "source", *, now: datetime | None = None
    ) -> dict[str, Any]:
        if group_by not in GROUP_BY:
            raise ValueError(f"group_by must be one of {', '.join(GROUP_BY)}")
        clock = now or datetime.now(UTC)
        all_facts = self.facts(user_id)
        groups: dict[str, list[ApplicationFacts]] = defaultdict(list)
        for facts in all_facts:
            groups[self._key(facts, group_by)].append(facts)
        rows = [self._row(key, members, clock) for key, members in groups.items()]
        rows.sort(key=lambda row: (-row["submitted"], row["group"]))
        return {
            "group_by": group_by,
            "maturity_days": MATURITY_DAYS,
            "low_sample_below": LOW_SAMPLE,
            "totals": self._row("all", all_facts, clock),
            "groups": rows,
            "notes": [
                "Rates use only mature submissions: sent at least "
                f"{MATURITY_DAYS} days ago, or already answered.",
                "Intervals are 95% Wilson intervals; groups with fewer than "
                f"{LOW_SAMPLE} mature submissions are flagged low_sample and never compared.",
                "The model score is only a grouping dimension here; these numbers do not show "
                "that it caused an outcome.",
            ],
        }

    @staticmethod
    def _key(facts: ApplicationFacts, group_by: str) -> str:
        if group_by == "source":
            return facts.source
        if group_by == "company":
            return _normalise(facts.company)
        if group_by == "resume":
            return facts.resume_label
        if group_by == "role":
            return _normalise(facts.title)
        if group_by == "score_band":
            return facts.score_band
        return facts.strategy

    @staticmethod
    def _row(group: str, members: list[ApplicationFacts], now: datetime) -> dict[str, Any]:
        submitted = [m for m in members if m.submitted]
        mature = [m for m in submitted if m.mature(now)]
        n = len(mature)

        def stat(predicate: str) -> RateStat:
            return rate(sum(1 for m in mature if getattr(m, predicate)), n)

        return {
            "group": group,
            "applications": len(members),
            "submitted": len(submitted),
            "mature_submitted": n,
            "pending": len(submitted) - n,
            "response": asdict(stat("responded")),
            "interview": asdict(stat("interview")),
            "offer": asdict(stat("offer")),
            "rejection": asdict(stat("rejected")),
        }

    def insights(self, user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Descriptive comparisons with their evidence. No recommendation is made here."""
        clock = now or datetime.now(UTC)
        findings: list[dict[str, Any]] = []
        for dimension in ("source", "resume", "score_band", "strategy", "role"):
            funnel = self.funnel(user_id, dimension, now=clock)
            usable = [
                g
                for g in funnel["groups"]
                if not g["response"]["low_sample"] and g["response"]["value"] is not None
            ]
            if len(usable) < 2:
                continue
            usable.sort(key=lambda g: g["response"]["value"], reverse=True)
            best, worst = usable[0], usable[-1]
            a = RateStat(**best["response"])
            b = RateStat(**worst["response"])
            findings.append(
                {
                    "dimension": dimension,
                    "best": {"group": best["group"], **best["response"]},
                    "worst": {"group": worst["group"], **worst["response"]},
                    "distinguishable": distinguishable(a, b),
                    "statement": (
                        f"By {dimension}, '{best['group']}' was answered more often "
                        f"({a.value:.0%} of {a.n}) than '{worst['group']}' "
                        f"({b.value:.0%} of {b.n})."
                        + (
                            ""
                            if distinguishable(a, b)
                            else " The intervals overlap, so this may be chance."
                        )
                    ),
                }
            )
        return {
            "findings": findings,
            "caution": "Descriptive only: correlation in a small personal sample, not a cause.",
        }
