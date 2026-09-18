"""Train/benchmark or shadow-run learning-to-rank on the frozen human label dataset.

    python scripts/ltr_pipeline.py train  --split gold-v1 [--allow-incomplete] [--final-test]
    python scripts/ltr_pipeline.py shadow --user-id U --resume-id R [--top-k 10]

``train`` refuses unless the split is frozen and the dataset is ready (``--allow-incomplete``
produces a *provisional* model that inference will not load). ``shadow`` needs
``APP_LTR_ENABLED=true`` and writes only the shadow columns; production scores are untouched.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.config import get_settings
from app.services.ltr_training import (
    LtrDisabled,
    LtrNotReady,
    run_shadow,
    train_and_evaluate,
)
from app.storage.database import SessionFactory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    train = commands.add_parser("train", help="fit on the train fold, benchmark on a frozen fold")
    train.add_argument("--split", required=True)
    train.add_argument("--output", default=None, help="artifact path (default: APP_LTR_MODEL_PATH)")
    train.add_argument("--allow-incomplete", action="store_true")
    train.add_argument("--final-test", action="store_true", help="score the test fold (once)")

    shadow = commands.add_parser("shadow", help="write shadow LTR scores for one resume")
    shadow.add_argument("--user-id", required=True)
    shadow.add_argument("--resume-id", required=True)
    shadow.add_argument("--top-k", type=int, default=10)

    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        with SessionFactory() as session:
            if args.command == "train":
                outcome = train_and_evaluate(
                    session,
                    split_name=args.split,
                    output_path=args.output or settings.ltr_model_path,
                    allow_incomplete=args.allow_incomplete,
                    final_test=args.final_test,
                )
                print(json.dumps(outcome.as_dict(), indent=2, default=str))
            else:
                summary = run_shadow(
                    session,
                    settings,
                    user_id=args.user_id,
                    resume_id=args.resume_id,
                    top_k=args.top_k,
                )
                session.commit()
                print(json.dumps(summary, indent=2))
    except (LtrNotReady, LtrDisabled) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
