"""Train, benchmark and shadow-run learning-to-rank on the frozen human label dataset.

Guard rails (each is enforced here, not left to the caller):

* training needs a **frozen split** and a dataset the coverage report calls ``ready``; an
  incomplete dataset can only produce a ``provisional`` artifact that inference refuses;
* training data is the ``train`` fold, metrics come from ``validation``; the ``test`` fold is
  scored only on an explicit final run and that fact is recorded in the artifact;
* inference is off unless ``APP_LTR_ENABLED`` is set, writes only the shadow columns
  (``ltr_score``, ``ltr_rank``, ``rank_delta``, ...) and never touches the production score.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.matching.cross_encoder.annotation import (
    load_features_from_db,
    resume_match_results_statement,
)
from app.matching.cross_encoder.features import MatchFeatures
from app.matching.cross_encoder.ltr_feature_contract import LTR_FEATURE_NAMES
from app.matching.ltr.baseline import (
    BenchmarkReport,
    CurrentPipelineRanker,
    LambdaMartRanker,
    LogisticRanker,
    RankedItem,
    RankingGroup,
    benchmark,
)
from app.matching.ltr.schema import (
    FEATURE_SCHEMA_VERSION,
    ModelArtifact,
    feature_schema_hash,
)
from app.services.annotation_dataset import dataset_report, export_fold
from app.storage.tables import ApplicationMatchResultRow


class LtrNotReady(RuntimeError):
    """The dataset or configuration does not allow this LTR operation."""


class LtrDisabled(RuntimeError):
    pass


def _features_dict(features: MatchFeatures) -> dict[str, float | None]:
    raw = features.to_dict()
    return {
        name: (None if raw.get(name) is None else float(raw[name])) for name in LTR_FEATURE_NAMES
    }


def build_groups(
    session: Session, *, split_name: str, fold: str, user_id: str | None = None
) -> list[RankingGroup]:
    """One ranking group per resume: the human-labelled vacancies of ``fold`` with features."""
    export = export_fold(session, split_name=split_name, fold=fold, user_id=user_id)
    by_resume: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in export["pointwise"]:
        by_resume.setdefault((row["user_id"], row["resume_id"]), []).append(row)

    groups: list[RankingGroup] = []
    for (owner, resume_id), rows in sorted(by_resume.items()):
        features = {f.vacancy_id: f for f in load_features_from_db(session, owner, resume_id)}
        items = []
        for row in sorted(rows, key=lambda r: r["vacancy_id"]):
            found = features.get(row["vacancy_id"])
            if found is None:
                continue  # labelled, but no scored match result to derive features from
            items.append(
                RankedItem(
                    vacancy_id=row["vacancy_id"],
                    features=_features_dict(found),
                    gain=int(row["gain"]),
                    baseline_score=found.existing_match_score,
                )
            )
        if items:
            groups.append(RankingGroup(group_id=resume_id, items=tuple(items)))
    return groups


@dataclass(slots=True)
class TrainingOutcome:
    artifact_path: str
    provisional: bool
    benchmark: BenchmarkReport
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self.benchmark),
            "artifact_path": self.artifact_path,
            "provisional": self.provisional,
            "warnings": self.warnings,
        }


def train_and_evaluate(
    session: Session,
    *,
    split_name: str,
    output_path: str | Path,
    allow_incomplete: bool = False,
    final_test: bool = False,
    include_lambdamart: bool | None = None,
) -> TrainingOutcome:
    report = dataset_report(session, split_name=split_name)
    split = report["split"]
    if split is None or not split["frozen"]:
        raise LtrNotReady("Freeze an evaluation split before training")
    if not report["ready"] and not allow_incomplete:
        raise LtrNotReady(
            "The human label dataset is not ready: " + "; ".join(report["warnings"] or ["unknown"])
        )

    eval_fold = "test" if final_test else "validation"
    train_groups = build_groups(session, split_name=split_name, fold="train")
    eval_groups = build_groups(session, split_name=split_name, fold=eval_fold)
    if not train_groups:
        raise LtrNotReady("The train fold has no labelled vacancies with features")

    logistic = LogisticRanker()
    rankers: list[Any] = [CurrentPipelineRanker(), logistic]
    use_lambdamart = (
        find_spec("lightgbm") is not None if include_lambdamart is None else include_lambdamart
    )
    if use_lambdamart:
        rankers.append(LambdaMartRanker())
    result = benchmark(train_groups, eval_groups, rankers, fold=eval_fold)

    artifact = ModelArtifact(
        model_type="logistic",
        feature_names=list(LTR_FEATURE_NAMES),
        schema_version=FEATURE_SCHEMA_VERSION,
        schema_hash=feature_schema_hash(),
        parameters=logistic.parameters(),
        trained_on={
            "split": split_name,
            "dataset_hash": report["dataset_hash"],
            "train_items": sum(len(g.items) for g in train_groups),
            "eval_fold": eval_fold,
            "test_evaluated": final_test,
        },
        metrics={"benchmark": asdict(result)},
        provisional=not report["ready"],
    )
    path = artifact.save(output_path)
    return TrainingOutcome(str(path), artifact.provisional, result, list(report["warnings"]))


class LtrScorer:
    """A loaded, schema-checked artifact used for shadow scoring and Top-N ranking."""

    def __init__(self, artifact: ModelArtifact) -> None:
        if artifact.model_type != "logistic":
            raise LtrNotReady(f"Unsupported model type for shadow scoring: {artifact.model_type}")
        self.artifact = artifact
        self._ranker = LogisticRanker.from_parameters(artifact.parameters, artifact.feature_names)

    @classmethod
    def from_settings(cls, settings: Settings) -> LtrScorer:
        if not settings.ltr_enabled:
            raise LtrDisabled("LTR is disabled (set APP_LTR_ENABLED=true to enable shadow scoring)")
        artifact = ModelArtifact.load(
            settings.ltr_model_path, allow_provisional=settings.ltr_allow_provisional
        )
        return cls(artifact)

    def rank_top_n(
        self, candidates: list[tuple[str, MatchFeatures]], n: int
    ) -> list[tuple[str, float]]:
        """Top-N vacancy ids by LTR score, best first (ties broken by id, deterministic)."""
        group = RankingGroup(
            "inference",
            tuple(RankedItem(vid, _features_dict(f), gain=0) for vid, f in candidates),
        )
        scores = self._ranker.score(group)
        ranked = sorted(
            zip((vid for vid, _ in candidates), scores, strict=True), key=lambda p: (-p[1], p[0])
        )
        return ranked[:n]


def run_shadow(
    session: Session,
    settings: Settings,
    *,
    user_id: str,
    resume_id: str,
    top_k: int = 10,
) -> dict[str, Any]:
    """Score a resume's pool with LTR and store ONLY the shadow columns.

    The production ``final_score`` and every status stay untouched; the caller commits.
    """
    scorer = LtrScorer.from_settings(settings)
    features = {f.vacancy_id: f for f in load_features_from_db(session, user_id, resume_id)}
    rows = session.execute(resume_match_results_statement(session, user_id, resume_id)).all()
    pool: dict[str, ApplicationMatchResultRow] = {}
    for match_result, vacancy, _application in rows:
        if vacancy.id in features and (
            vacancy.id not in pool
            or (match_result.final_score or 0) > (pool[vacancy.id].final_score or 0)
        ):
            pool[vacancy.id] = match_result
    if not pool:
        return {"scored": 0, "top_k": top_k, "overlap": 0}

    ranked = scorer.rank_top_n([(vid, features[vid]) for vid in pool], n=len(pool))
    ltr_rank = {vid: rank for rank, (vid, _) in enumerate(ranked, start=1)}
    ltr_score = dict(ranked)
    current = sorted(pool, key=lambda vid: (-(pool[vid].final_score or 0.0), vid))
    current_rank = {vid: rank for rank, vid in enumerate(current, start=1)}
    overlap = len(set(current[:top_k]) & {vid for vid, _ in ranked[:top_k]})

    for vid, match_result in pool.items():
        match_result.ltr_score = ltr_score[vid]
        match_result.ltr_status = "shadow"
        match_result.ltr_rank = ltr_rank[vid]
        match_result.rank_delta = current_rank[vid] - ltr_rank[vid]
        match_result.ltr_topk_overlap = {"k": top_k, "overlap": overlap}
    session.flush()
    return {"scored": len(pool), "top_k": top_k, "overlap": overlap}


def latest_shadow_summary(session: Session, resume_id: str) -> dict[str, Any]:
    """How many results of a resume carry shadow scores (for dashboards and scripts)."""
    statement = select(ApplicationMatchResultRow.ltr_status).where(
        ApplicationMatchResultRow.cv_file_id == resume_id
    )
    statuses = [s for s in session.execute(statement).scalars() if s is not None]
    return {"shadow_scored": sum(1 for s in statuses if s == "shadow")}
