#!/usr/bin/env python
"""
Generate LTR dataset from human annotations.

Combines pointwise and pairwise annotations from the database
into the canonical ltr_dataset.jsonl format for training.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching.cross_encoder.features import FeatureExtractor, MatchFeatures
from app.matching.cross_encoder.ltr_feature_contract import LTR_FEATURE_NAMES
from app.matching.cross_encoder.normalization import ScoreNormalizer
from app.storage.database import session_scope
from app.storage.tables import (
    AnnotationFeedbackRow,
    ApplicationMatchResultRow,
    ApplicationRow,
    CvFileRow,
    UserRow,
    VacancyRow,
)


def load_vacancy_meta() -> dict[str, dict]:
    """Load vacancy metadata from pre-computed file."""
    import json as _json
    from pathlib import Path
    from collections import defaultdict

    vacancies_path = Path("data/matching/vacancies.json")
    if not vacancies_path.exists():
        return {}

    meta: dict[str, dict] = {}
    with open(vacancies_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    v = _json.loads(line)
                    meta[v.get("id", "")] = v
                except _json.JSONDecodeError:
                    pass
    return meta


def load_cross_encoder_scores(session: Session) -> dict[str, dict[str, float]]:
    """Load cross-encoder scores from ApplicationMatchResultRow."""
    from collections import defaultdict

    # Get all match results with LTR features
    stmt = select(
        ApplicationMatchResultRow,
        ApplicationRow.selected_cv_file_id,
        ApplicationRow.vacancy_id,
    ).join(ApplicationRow, ApplicationMatchResultRow.application_id == ApplicationRow.id)
    results = session.execute(stmt).all()

    ce_scores: dict[str, dict[str, float]] = defaultdict(dict)
    for match_result, cv_file_id, vacancy_id in results:
        if cv_file_id is None:
            continue
        key = f"{cv_file_id}|{vacancy_id}"
        # We need to extract CE scores from the stored data
        # This would need to be populated from the full_corpus_scores.jsonl
        pass

    return dict(ce_scores)


def load_requirement_matches(session: Session) -> dict[str, list[dict]]:
    """Load requirement matches for all vacancies."""
    # This would load from the requirement_matches table or pre-computed file
    # For now, return empty - will be populated from full_corpus_scores.jsonl
    return {}


def build_dataset_from_db(session: Session) -> list[dict[str, Any]]:
    """Build LTR dataset from database annotations."""
    # Load all annotations
    pw_stmt = select(AnnotationFeedbackRow).where(
        AnnotationFeedbackRow.feedback_type == "pointwise"
    )
    pw_annotations = session.execute(pw_stmt).scalars().all()

    pws_stmt = select(AnnotationFeedbackRow).where(
        AnnotationFeedbackRow.feedback_type == "pairwise"
    )
    pws_annotations = session.execute(pws_stmt).scalars().all()

    # Load all match results to get features
    # Join ApplicationMatchResultRow with ApplicationRow and VacancyRow
    stmt = (
        select(ApplicationMatchResultRow, ApplicationRow, VacancyRow, CvFileRow)
        .join(ApplicationRow, ApplicationMatchResultRow.application_id == ApplicationRow.id)
        .join(VacancyRow, ApplicationRow.vacancy_id == VacancyRow.id)
        .join(CvFileRow, ApplicationRow.selected_cv_file_id == CvFileRow.id)
    )
    match_results = session.execute(stmt).all()

    # Build lookup: (resume_id, vacancy_id) -> match_result
    match_lookup: dict[tuple[str, str], ApplicationMatchResultRow] = {}
    for mr, app, vac, cv in match_results:
        if cv.id and app.vacancy_id:
            match_lookup[(cv.id, app.vacancy_id)] = mr

    # Load cross-encoder scores from pre-computed data
    import json as _json
    from pathlib import Path
    from collections import defaultdict

    corpus_path = Path("data/matching/full_corpus_scores.jsonl")
    req_path = Path("data/matching/req_matches.json")
    vacancies_path = Path("data/matching/vacancies.json")

    ce_scores: dict[str, dict[str, float]] = defaultdict(dict)
    if corpus_path.exists():
        with open(corpus_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        row = _json.loads(line)
                        rid = row.get("resume_id")
                        vid = row.get("vacancy_id")
                        if rid and vid:
                            key = f"{rid}|{vid}"
                            for col in ["ce_ettin_raw", "ce_mmbert_raw", "ce_modernbert_raw"]:
                                val = row.get(col)
                                if val is not None:
                                    model_name = col.replace("ce_", "")
                                    ce_scores[model_name][key] = val
                    except _json.JSONDecodeError:
                        pass

    # Load requirement matches
    req_by_vacancy: dict[str, list[dict]] = defaultdict(list)
    if req_path.exists():
        with open(req_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rm = _json.loads(line)
                        req_by_vacancy[rm.get("vacancy_id", "")].append(rm)
                    except _json.JSONDecodeError:
                        pass

    # Load vacancy meta
    vacancy_meta: dict[str, dict] = {}
    if vacancies_path.exists():
        with open(vacancies_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        v = _json.loads(line)
                        vacancy_meta[v.get("id", "")] = v
                    except _json.JSONDecodeError:
                        pass

    # Build existing scores
    existing_scores: dict[str, dict] = {}
    for mr, app, vac, cv in match_results:
        if cv.id and app.vacancy_id:
            key = f"{cv.id}|{app.vacancy_id}"
            existing_scores[key] = {
                "match_score": mr.final_score,
                "reranker_score": mr.reranker_score,
                "semantic_similarity": mr.semantic_similarity,
            }

    # Fit normalizer
    normalizer = ScoreNormalizer(method="rank")
    for model_name, scores in ce_scores.items():
        normalizer.fit(model_name, list(scores.values()))

    # Create feature extractor
    extractor = FeatureExtractor(
        requirement_matches=req_by_vacancy,
        existing_scores=existing_scores,
        vacancy_meta=vacancy_meta,
        cross_encoder_scores=ce_scores,
    )

    # Collect all resume_ids that have annotations
    annotated_resume_ids = set()
    for a in pw_annotations:
        annotated_resume_ids.add(a.resume_id)
    for a in pws_annotations:
        annotated_resume_ids.add(a.resume_id)

    # Build dataset rows
    dataset_rows: list[dict[str, Any]] = []

    # Process pointwise annotations
    for ann in pw_annotations:
        resume_id = ann.resume_id
        vacancy_id = ann.vacancy_id
        mr = match_lookup.get((resume_id, vacancy_id))
        if not mr:
            continue

        features = extractor.extract(resume_id, vacancy_id)
        features.ce_ettin_norm = normalizer.normalize("ettin-reranker-68m-v1", features.ce_ettin_raw)
        features.ce_mmbert_norm = normalizer.normalize("mmBERT-small", features.ce_mmbert_raw)
        features.ce_modernbert_norm = normalizer.normalize("multilingual-modernbert-small", features.ce_modernbert_raw)

        # Map pointwise label to human_signal
        label_map = {"relevant": 1.0, "maybe": 0.5, "not_relevant": 0.0}
        human_signal = label_map.get(ann.label, 0.0)

        row = {
            "resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "human_signal": human_signal,
        }
        # Add all 26 features
        for fn in LTR_FEATURE_NAMES:
            val = getattr(features, fn, None)
            row[fn] = val if val is not None else 0.0

        dataset_rows.append(row)

    # Process pairwise annotations
    # For pairwise, we create two rows: one for A, one for B
    for ann in pws_annotations:
        resume_id = ann.resume_id
        vacancy_a = ann.vacancy_a_id
        vacancy_b = ann.vacancy_b_id
        
        mr_a = match_lookup.get((resume_id, vacancy_a)) if vacancy_a else None
        mr_b = match_lookup.get((resume_id, vacancy_b)) if vacancy_b else None
        
        if not mr_a or not mr_b:
            continue

        # Extract features for both
        features_a = extractor.extract(resume_id, vacancy_a)
        features_a.ce_ettin_norm = normalizer.normalize("ettin-reranker-68m-v1", features_a.ce_ettin_raw)
        features_a.ce_mmbert_norm = normalizer.normalize("mmBERT-small", features_a.ce_mmbert_raw)
        features_a.ce_modernbert_norm = normalizer.normalize("multilingual-modernbert-small", features_a.ce_modernbert_raw)
        
        features_b = extractor.extract(resume_id, vacancy_b)
        features_b.ce_ettin_norm = normalizer.normalize("ettin-reranker-68m-v1", features_b.ce_ettin_raw)
        features_b.ce_mmbert_norm = normalizer.normalize("mmBERT-small", features_b.ce_mmbert_raw)
        features_b.ce_modernbert_norm = normalizer.normalize("multilingual-modernbert-small", features_b.ce_modernbert_raw)

        # Map pairwise label to signals
        # a_better: A=1.0, B=0.0
        # b_better: A=0.0, B=1.0
        # both_equal: A=0.5, B=0.5
        # neither: A=0.0, B=0.0
        pairwise_map = {
            "a_better": (1.0, 0.0),
            "b_better": (0.0, 1.0),
            "both_equal": (0.5, 0.5),
            "neither": (0.0, 0.0),
        }
        signal_a, signal_b = pairwise_map.get(ann.label, (0.0, 0.0))

        # Row for A
        row_a = {
            "resume_id": resume_id,
            "vacancy_id": vacancy_a,
            "human_signal": signal_a,
        }
        for fn in LTR_FEATURE_NAMES:
            val = getattr(features_a, fn, None)
            row_a[fn] = val if val is not None else 0.0
        dataset_rows.append(row_a)

        # Row for B
        row_b = {
            "resume_id": resume_id,
            "vacancy_id": vacancy_b,
            "human_signal": signal_b,
        }
        for fn in LTR_FEATURE_NAMES:
            val = getattr(features_b, fn, None)
            row_b[fn] = val if val is not None else 0.0
        dataset_rows.append(row_b)

    return dataset_rows


def main():
    output_path = Path("data/matching/ltr_dataset.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with session_scope() as session:
        dataset = build_dataset_from_db(session)

    # Write JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for row in dataset:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Generated {len(dataset)} rows in {output_path}")

    # Print statistics
    if dataset:
        signals = [r["human_signal"] for r in dataset]
        from collections import Counter
        dist = Counter(signals)
        print(f"Signal distribution: {dict(dist)}")
        
        resume_ids = set(r["resume_id"] for r in dataset)
        print(f"Unique resumes: {len(resume_ids)}")


if __name__ == "__main__":
    main()
