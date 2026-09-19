"""Closed-loop job strategy: evidence-backed recommendations the user accepts or rejects.

The loop: search -> match -> apply -> outcome (CRM) -> learning (this module) -> strategy (a
recommendation the user decides on) -> the next round of outcomes, which ``followup`` compares with
the moment of the decision.

Hard rules:
* a recommendation exists only when the CRM shows two groups with enough matured submissions and
  **non-overlapping** intervals; otherwise ``generate`` returns no recommendation and says what is
  missing - a small or lopsided sample is never turned into advice;
* every recommendation carries its evidence (groups, n, rates, intervals) and an explicit
  ``causal: false`` plus the confounders a reader must keep in mind;
* nothing changes without a decision. ``accept`` applies exactly one narrow, reversible action
  (switching the active resume) or records advice the user adopts themselves; ``reject`` is
  remembered and the same recommendation is not proposed again;
* decisions are stored forever as the audit trail of the strategy.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.crm_stats import LOW_SAMPLE, RateStat, distinguishable, rate
from app.services.application_crm import ApplicationCrmService
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import CvFileRow, StrategyRecommendationRow, UserRow

MIN_TOTAL_MATURE = 30  # below this the whole history is too thin to compare anything
DIMENSION_KIND = {
    "resume": "prefer_resume",
    "source": "prefer_source",
    "role": "target_role",
    "score_band": "review_score_band",
}
CONFOUNDERS = {
    "resume": "Different resumes were probably used on different kinds of vacancies.",
    "source": "Sources differ in vacancy mix, seniority and how easy it is to reply.",
    "role": "Roles differ in demand, and a few applications per role is a small sample.",
    "score_band": (
        "The score band groups by the model's own opinion, not by anything the employer saw."
    ),
}


class RecommendationClosed(ValueError):
    """The recommendation was already decided."""


def _fingerprint(user_id: str, kind: str, dimension: str, best: str, worst: str) -> str:
    payload = "|".join((user_id, kind, dimension, best, worst))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class JobStrategyService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._crm = ApplicationCrmService(session)

    def _require_user(self, user_id: str) -> None:
        if self._session.get(UserRow, user_id) is None:
            raise EntityNotFoundError("User not found")

    # ── generation ──────────────────────────────────────────────────────────

    def generate(self, user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        self._require_user(user_id)
        clock = now or datetime.now(UTC)
        overall = self._crm.funnel(user_id, "source", now=clock)["totals"]
        mature = overall["mature_submitted"]
        created: list[StrategyRecommendationRow] = []
        skipped: list[str] = []
        missing: list[str] = []

        if mature < MIN_TOTAL_MATURE:
            missing.append(
                f"Only {mature} matured submissions; at least {MIN_TOTAL_MATURE} are needed "
                "before any comparison is meaningful."
            )
        else:
            for dimension, kind in DIMENSION_KIND.items():
                funnel = self._crm.funnel(user_id, dimension, now=clock)
                usable = [
                    g
                    for g in funnel["groups"]
                    if not g["response"]["low_sample"] and g["response"]["value"] is not None
                ]
                if len(usable) < 2:
                    missing.append(
                        f"By {dimension}: fewer than two groups have {LOW_SAMPLE}+ "
                        "matured submissions."
                    )
                    continue
                usable.sort(key=lambda g: g["response"]["value"], reverse=True)
                best, worst = usable[0], usable[-1]
                a, b = RateStat(**best["response"]), RateStat(**worst["response"])
                if not distinguishable(a, b):
                    missing.append(
                        f"By {dimension}: '{best['group']}' ({a.value:.0%}) and '{worst['group']}' "
                        f"({b.value:.0%}) are not distinguishable yet (their intervals overlap)."
                    )
                    continue
                row = self._propose(user_id, kind, dimension, best, worst, clock)
                if row is None:
                    skipped.append(f"{dimension}: already proposed or rejected earlier")
                else:
                    created.append(row)
        return {
            "created": [self.serialise(row) for row in created],
            "skipped": skipped,
            "no_recommendation_because": missing,
            "matured_submissions": mature,
        }

    def _propose(
        self,
        user_id: str,
        kind: str,
        dimension: str,
        best: dict[str, Any],
        worst: dict[str, Any],
        now: datetime,
    ) -> StrategyRecommendationRow | None:
        fingerprint = _fingerprint(user_id, kind, dimension, best["group"], worst["group"])
        existing = self._session.scalar(
            select(StrategyRecommendationRow).where(
                StrategyRecommendationRow.user_id == user_id,
                StrategyRecommendationRow.fingerprint == fingerprint,
            )
        )
        if existing is not None:
            return None  # proposed, accepted or rejected before: a decision is never re-asked
        a, b = best["response"], worst["response"]
        payload: dict[str, Any] = {
            "action": "advice",
            "prefer": best["group"],
            "over": worst["group"],
        }
        if kind == "prefer_resume":
            resume_id = self._resume_id_for(user_id, best["group"])
            if resume_id is not None:
                payload = {
                    "action": "set_active_resume",
                    "resume_id": resume_id,
                    "label": best["group"],
                }
        row = StrategyRecommendationRow(
            user_id=user_id,
            kind=kind,
            dimension=dimension,
            fingerprint=fingerprint,
            statement=(
                f"By {dimension}, '{best['group']}' was answered {a['value']:.0%} of the time "
                f"({a['successes']}/{a['n']}) versus {b['value']:.0%} ({b['successes']}/{b['n']}) "
                f"for '{worst['group']}', and the intervals do not overlap."
            ),
            payload=payload,
            evidence={
                "best": {"group": best["group"], **a},
                "worst": {"group": worst["group"], **b},
                "maturity_days": 14,
                "causal": False,
                "confounders": [CONFOUNDERS[dimension]],
            },
            status="proposed",
            created_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def _resume_id_for(self, user_id: str, label: str) -> str | None:
        rows = self._session.execute(
            select(CvFileRow.id).where(
                CvFileRow.user_id == user_id, CvFileRow.original_filename == label
            )
        ).all()
        return rows[0][0] if len(rows) == 1 else None  # ambiguous file names stay advice

    # ── decisions ───────────────────────────────────────────────────────────

    def list(self, user_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        self._require_user(user_id)
        statement = select(StrategyRecommendationRow).where(
            StrategyRecommendationRow.user_id == user_id
        )
        if status:
            statement = statement.where(StrategyRecommendationRow.status == status)
        rows = self._session.scalars(
            statement.order_by(
                StrategyRecommendationRow.created_at.desc(), StrategyRecommendationRow.id
            )
        )
        return [self.serialise(row) for row in rows]

    def _owned(self, user_id: str, recommendation_id: str) -> StrategyRecommendationRow:
        row = self._session.get(StrategyRecommendationRow, recommendation_id)
        if row is None or row.user_id != user_id:
            raise EntityNotFoundError("Recommendation not found")
        return row

    def decide(
        self,
        user_id: str,
        recommendation_id: str,
        *,
        decision: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if decision not in {"accept", "reject"}:
            raise ValueError("decision must be 'accept' or 'reject'")
        row = self._owned(user_id, recommendation_id)
        if row.status != "proposed":
            raise RecommendationClosed(f"This recommendation was already {row.status}")
        clock = now or datetime.now(UTC)
        applied: dict[str, Any] = {}
        if decision == "accept":
            applied = self._apply(user_id, row)
            row.baseline = self._baseline(user_id, row, clock)
            row.status = "accepted"
        else:
            row.status = "rejected"
        row.decided_at = clock
        row.decision_note = (note or "").strip()[:1000] or None
        row.applied = applied
        self._session.flush()
        return self.serialise(row)

    def _apply(self, user_id: str, row: StrategyRecommendationRow) -> dict[str, Any]:
        """Perform the one action a recommendation may carry - only after acceptance."""
        action = row.payload.get("action")
        if action != "set_active_resume":
            return {"action": "advice", "changed": False}
        resume = self._session.get(CvFileRow, str(row.payload.get("resume_id")))
        user = self._session.get(UserRow, user_id)
        if resume is None or user is None or resume.user_id != user_id:
            raise EntityNotFoundError("The recommended resume no longer exists")
        previous = user.active_cv_file_id
        user.active_cv_file_id = resume.id
        return {"action": action, "changed": previous != resume.id, "previous_resume_id": previous}

    def _baseline(
        self, user_id: str, row: StrategyRecommendationRow, now: datetime
    ) -> dict[str, Any]:
        overall = self._crm.funnel(user_id, "source", now=now)["totals"]
        return {
            "at": now.isoformat(),
            "overall_response": overall["response"],
            "mature_submitted": overall["mature_submitted"],
        }

    # ── follow-up (closing the loop) ────────────────────────────────────────

    def followup(
        self, user_id: str, recommendation_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """Did answers improve after the user adopted the recommendation? Descriptive only."""
        row = self._owned(user_id, recommendation_id)
        if row.status != "accepted" or row.decided_at is None:
            return {
                "state": "not_accepted",
                "message": "Only accepted recommendations have a follow-up.",
            }
        clock = now or datetime.now(UTC)
        decided = row.decided_at if row.decided_at.tzinfo else row.decided_at.replace(tzinfo=UTC)
        facts = [f for f in self._crm.facts(user_id) if f.submitted]
        after = [
            f for f in facts if (f.submitted_at or f.created_at) >= decided and f.mature(clock)
        ]
        before = [
            f for f in facts if (f.submitted_at or f.created_at) < decided and f.mature(clock)
        ]
        after_stat = rate(sum(1 for f in after if f.responded), len(after))
        before_stat = rate(sum(1 for f in before if f.responded), len(before))
        if after_stat.low_sample:
            return {
                "state": "too_early",
                "message": f"Only {after_stat.n} matured submissions since the decision; "
                f"{LOW_SAMPLE} are needed before a comparison means anything.",
                "before": asdict(before_stat),
                "after": asdict(after_stat),
            }
        changed = distinguishable(before_stat, after_stat)
        return {
            "state": "compared",
            "before": asdict(before_stat),
            "after": asdict(after_stat),
            "distinguishable": changed,
            "message": (
                "The response rate differs beyond chance, but other things changed too."
                if changed
                else "No difference beyond chance so far."
            ),
            "causal": False,
        }

    @staticmethod
    def serialise(row: StrategyRecommendationRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "kind": row.kind,
            "dimension": row.dimension,
            "statement": row.statement,
            "payload": row.payload,
            "evidence": row.evidence,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "decision_note": row.decision_note,
            "applied": row.applied,
            "baseline": row.baseline,
        }
