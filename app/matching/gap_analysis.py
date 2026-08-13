"""Profile gap analysis after matching.

Distinguishes real skill gaps from evidence gaps where the
candidate has experience but it isn't sufficiently documented.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class GapType(StrEnum):
    SKILL_GAP = "skill_gap"
    EVIDENCE_GAP = "evidence_gap"
    DURATION_GAP = "duration_gap"
    METADATA_GAP = "metadata_gap"
    EVALUATION_ERROR = "evaluation_error"


class StrictGapModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProfileGap(StrictGapModel):
    """A single gap identified in the candidate profile."""

    gap_type: GapType
    claim_id: str
    claim_subject: str
    description: str = Field(max_length=2_000)
    suggested_action: str = Field(max_length=2_000)
    confidence: float = Field(ge=0, le=1)


class GapAnalysisResult(StrictGapModel):
    """Full gap analysis result."""

    gaps: list[ProfileGap] = Field(default_factory=list, max_length=100)
    total_gaps: int = Field(ge=0)
    skill_gaps: int = Field(ge=0)
    evidence_gaps: int = Field(ge=0)
    duration_gaps: int = Field(ge=0)
    metadata_gaps: int = Field(ge=0)
    evaluation_errors: int = Field(ge=0, default=0)


def analyze_gaps(
    claim_evaluations: list[dict[str, object]],
) -> GapAnalysisResult:
    """Analyze gaps from claim evaluation results.

    Each dict in claim_evaluations should have:
    - claim_id: str
    - claim_subject: str
    - claim_type: str
    - relation: str (entailment relation)
    - evidence_strength: float
    - has_evidence: bool
    - duration_result: dict | None (for duration claims)
    """
    gaps: list[ProfileGap] = []

    for eval_data in claim_evaluations:
        claim_id = str(eval_data.get("claim_id", ""))
        claim_subject = str(eval_data.get("claim_subject", ""))
        claim_type = str(eval_data.get("claim_type", ""))
        relation = str(eval_data.get("relation", "unknown"))
        has_evidence = bool(eval_data.get("has_evidence", False))
        evidence_strength = float(eval_data.get("evidence_strength", 0))
        duration_result = eval_data.get("duration_result")

        if relation == "entailed":
            continue

        if relation == "contradicted":
            gaps.append(
                ProfileGap(
                    gap_type=GapType.SKILL_GAP,
                    claim_id=claim_id,
                    claim_subject=claim_subject,
                    description=f"Contradicting evidence found for: {claim_subject}",
                    suggested_action="Remove or correct contradicting evidence in profile",
                    confidence=0.9,
                )
            )
            continue

        # Evaluation error — technical failure, not a skill gap
        if relation == "evaluation_error":
            gaps.append(
                ProfileGap(
                    gap_type=GapType.EVALUATION_ERROR,
                    claim_id=claim_id,
                    claim_subject=claim_subject,
                    description=(
                        f"Could not evaluate {claim_subject} due to a technical error. "
                        "This does not mean the skill is missing."
                    ),
                    suggested_action="Retry later. Check evaluator config if persists.",
                    confidence=0.0,
                )
            )
            continue

        # Duration claims
        if claim_type == "experience_duration" and duration_result is not None:
            actual = float(duration_result.get("actual_years", 0))
            required = float(duration_result.get("required_years", 0))
            if actual > 0 and actual < required:
                gaps.append(
                    ProfileGap(
                        gap_type=GapType.DURATION_GAP,
                        claim_id=claim_id,
                        claim_subject=claim_subject,
                        description=(
                            f"Experience with {claim_subject} found ({actual:.1f} years) "
                            f"but required {required:.1f} years. "
                            "Dates may need to be added or clarified."
                        ),
                        suggested_action=(
                            f"Add specific dates to existing {claim_subject} "
                            "experience entries to verify duration"
                        ),
                        confidence=0.8,
                    )
                )
                continue
            if actual == 0:
                # No dated evidence — evidence gap, not skill gap
                gaps.append(
                    ProfileGap(
                        gap_type=GapType.EVIDENCE_GAP,
                        claim_id=claim_id,
                        claim_subject=claim_subject,
                        description=(
                            f"No dated evidence found for {claim_subject} experience. "
                            "Cannot verify duration requirement."
                        ),
                        suggested_action=(
                            f"Add dated {claim_subject} experience entries with start and end dates"
                        ),
                        confidence=0.7,
                    )
                )
                continue

        # Insufficient evidence — data is unclear, not necessarily missing
        if relation == "insufficient_evidence":
            gaps.append(
                ProfileGap(
                    gap_type=GapType.EVIDENCE_GAP,
                    claim_id=claim_id,
                    claim_subject=claim_subject,
                    description=(
                        f"Insufficient data to verify {claim_subject}. "
                        "The skill may exist but is not documented clearly enough."
                    ),
                    suggested_action=(
                        f"Add explicit experience entries demonstrating {claim_subject} "
                        "with concrete project descriptions and dates"
                    ),
                    confidence=0.5,
                )
            )
            continue

        # Related but insufficient — evidence exists but doesn't confirm the claim
        if has_evidence and relation in ("partial", "related_but_insufficient"):
            gaps.append(
                ProfileGap(
                    gap_type=GapType.EVIDENCE_GAP,
                    claim_id=claim_id,
                    claim_subject=claim_subject,
                    description=(
                        f"Found related evidence for {claim_subject} "
                        f"(strength: {evidence_strength:.0%}) "
                        "but it doesn't explicitly confirm the claim."
                    ),
                    suggested_action=(
                        f"Clarify or expand existing experience entries "
                        f"to more explicitly demonstrate {claim_subject}"
                    ),
                    confidence=0.7,
                )
            )
            continue

        # Metadata gap
        if has_evidence and evidence_strength > 0.3:
            gaps.append(
                ProfileGap(
                    gap_type=GapType.METADATA_GAP,
                    claim_id=claim_id,
                    claim_subject=claim_subject,
                    description=(
                        f"Some evidence found for {claim_subject} but "
                        "missing structured data (dates, context, project details)."
                    ),
                    suggested_action=(
                        "Add dates, project names, and specific outcomes "
                        f"to {claim_subject} experience entries"
                    ),
                    confidence=0.6,
                )
            )
            continue

        # True skill gap: no evidence at all
        if not has_evidence or relation in ("unknown", "missing"):
            gaps.append(
                ProfileGap(
                    gap_type=GapType.SKILL_GAP,
                    claim_id=claim_id,
                    claim_subject=claim_subject,
                    description=f"No evidence found for: {claim_subject}",
                    suggested_action=(
                        f"This appears to be a genuine gap. "
                        f"Consider gaining experience with {claim_subject}"
                    ),
                    confidence=0.85,
                )
            )

    skill_gaps = sum(1 for g in gaps if g.gap_type == GapType.SKILL_GAP)
    evidence_gaps = sum(1 for g in gaps if g.gap_type == GapType.EVIDENCE_GAP)
    duration_gaps = sum(1 for g in gaps if g.gap_type == GapType.DURATION_GAP)
    metadata_gaps = sum(1 for g in gaps if g.gap_type == GapType.METADATA_GAP)
    evaluation_errors = sum(1 for g in gaps if g.gap_type == GapType.EVALUATION_ERROR)

    return GapAnalysisResult(
        gaps=gaps,
        total_gaps=len(gaps),
        skill_gaps=skill_gaps,
        evidence_gaps=evidence_gaps,
        duration_gaps=duration_gaps,
        metadata_gaps=metadata_gaps,
        evaluation_errors=evaluation_errors,
    )
