from app.matching.extraction import (
    ExperienceLevel,
    RequirementImportance,
    RequirementType,
)
from app.matching.scoring import (
    DeterministicMatchScorer,
    EligibilityStatus,
    MatchLevel,
    RequirementAssessment,
)


def _assessment(
    requirement_id: str,
    *,
    requirement_type: RequirementType = RequirementType.HARD_SKILL,
    importance: RequirementImportance = RequirementImportance.REQUIRED,
    match_level: MatchLevel = MatchLevel.EXACT,
    is_blocker: bool = False,
    experience_level: ExperienceLevel | None = ExperienceLevel.PRODUCTION,
) -> RequirementAssessment:
    return RequirementAssessment(
        requirement_id=requirement_id,
        requirement_type=requirement_type,
        importance=importance,
        weight=1,
        is_blocker=is_blocker,
        match_level=match_level,
        evidence_experience_level=experience_level,
    )


def test_score_is_reproducible_for_identical_inputs() -> None:
    scorer = DeterministicMatchScorer()
    assessments = (
        _assessment("python"),
        _assessment(
            "postgres",
            importance=RequirementImportance.PREFERRED,
            match_level=MatchLevel.STRONG,
        ),
    )

    assert scorer.score(assessments) == scorer.score(assessments)
    assert scorer.score(assessments).final_score == 97


def test_missing_required_requirement_caps_score_and_requires_review() -> None:
    score = DeterministicMatchScorer().score(
        (
            _assessment("python"),
            _assessment("kubernetes", match_level=MatchLevel.MISSING),
            _assessment(
                "postgres",
                importance=RequirementImportance.PREFERRED,
                match_level=MatchLevel.EXACT,
            ),
        )
    )

    assert score.final_score < 80  # Proportional cap, not fixed at 49
    assert score.eligibility_status is EligibilityStatus.REVIEW
    assert score.missing_required_count == 1


def test_hard_blocker_cannot_be_compensated_by_other_matches() -> None:
    score = DeterministicMatchScorer().score(
        (
            _assessment(
                "authorization",
                requirement_type=RequirementType.WORK_AUTHORIZATION,
                match_level=MatchLevel.MISSING,
                is_blocker=True,
                experience_level=None,
            ),
            _assessment("python"),
            _assessment("fastapi"),
            _assessment("postgres"),
        )
    )

    assert score.final_score <= 20
    assert score.eligibility_status is EligibilityStatus.INELIGIBLE
    assert score.blocker_count == 1
    assert "work authorization" in " ".join(score.explanation).casefold()


def test_conceptual_knowledge_is_not_scored_as_hands_on_experience() -> None:
    conceptual = DeterministicMatchScorer().score(
        (
            _assessment(
                "kubernetes",
                match_level=MatchLevel.EXACT,
                experience_level=ExperienceLevel.CONCEPTUAL,
            ),
        )
    )
    production = DeterministicMatchScorer().score((_assessment("kubernetes"),))

    assert conceptual.final_score == 15
    assert production.final_score == 100


def test_zero_weight_input_returns_bounded_zero_score() -> None:
    assessment = RequirementAssessment(
        requirement_id="optional",
        requirement_type=RequirementType.OTHER,
        importance=RequirementImportance.OPTIONAL,
        weight=0,
        is_blocker=False,
        match_level=MatchLevel.EXACT,
    )

    score = DeterministicMatchScorer().score((assessment,))

    assert score.final_score == 0
    assert score.eligibility_status is EligibilityStatus.ELIGIBLE


def test_language_score_computed_from_language_requirements() -> None:
    score = DeterministicMatchScorer().score(
        (
            _assessment(
                "english",
                requirement_type=RequirementType.LANGUAGE,
                match_level=MatchLevel.EXACT,
                experience_level=None,
            ),
            _assessment(
                "german",
                requirement_type=RequirementType.LANGUAGE,
                importance=RequirementImportance.PREFERRED,
                match_level=MatchLevel.PARTIAL,
                experience_level=None,
            ),
        )
    )

    assert score.language_score == 82
    assert score.eligibility_status is EligibilityStatus.ELIGIBLE


def test_language_score_zero_when_no_language_requirements() -> None:
    score = DeterministicMatchScorer().score(
        (_assessment("python"),),
    )

    assert score.language_score == 0
