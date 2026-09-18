"""Replay the current pipeline and the LTR challenger on a frozen human-labelled fold.

    python scripts/matching_ab.py --split gold-v1 [--fold validation|test] [--allow-provisional]

Both variants score the same groups. The *current pipeline* variant reads the scores already
stored by the LLM-heavy pipeline, so its latency here is the cost of a lookup, NOT of running the
LLM; only quality is comparable for it. The cost figures of the challenger are real. The report
also shows the margin curve for hybrid routing (LLM share vs how many boundary errors it would
catch) computed from the challenger's scores and the human labels.

Nothing is written to the database and no production setting changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from app.config import get_settings
from app.matching.ab.harness import run_ab
from app.matching.ab.routing import choose_margin, margin_curve
from app.matching.ltr.baseline import LogisticRanker, RankingGroup
from app.matching.ltr.schema import ModelArtifact, ModelArtifactError
from app.services.ltr_training import build_groups
from app.storage.database import SessionFactory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", required=True)
    parser.add_argument("--fold", default="validation", choices=["validation", "test"])
    parser.add_argument("--allow-provisional", action="store_true")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        artifact = ModelArtifact.load(
            settings.ltr_model_path, allow_provisional=args.allow_provisional
        )
    except (FileNotFoundError, ModelArtifactError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    ranker = LogisticRanker.from_parameters(artifact.parameters, artifact.feature_names)

    with SessionFactory() as session:
        groups = build_groups(session, split_name=args.split, fold=args.fold)

    def current(group: RankingGroup) -> list[float]:
        return [item.baseline_score or 0.0 for item in group.items]

    report = run_ab(
        {"current_pipeline": current, "ltr_logistic": ranker.score},
        groups,
        baseline="current_pipeline",
        k=args.top_k,
    )
    curve = margin_curve(groups, ranker.score, [0.0, 0.1, 0.25, 0.5, 1.0, 2.0], top_k=args.top_k)
    chosen = choose_margin(curve)
    print(
        json.dumps(
            {
                "fold": args.fold,
                "provisional_model": artifact.provisional,
                "ab": asdict(report),
                "margin_curve": [asdict(point) for point in curve],
                "recommended_margin": asdict(chosen) if chosen else None,
                "note": "current_pipeline latency is a stored-score lookup, not LLM cost",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
