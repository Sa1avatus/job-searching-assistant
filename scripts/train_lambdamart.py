#!/usr/bin/env python
"""
Train LightGBM LambdaMART baseline on human annotation dataset
with proper Leave-One-Group-Out cross-validation.

Uses canonical LTR_FEATURE_NAMES from app.matching.cross_encoder.ltr_feature_contract
for training/runtime feature contract consistency.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
from sklearn.metrics import ndcg_score

from app.matching.cross_encoder.ltr_feature_contract import (
    LTR_FEATURE_NAMES,
    LTR_FEATURE_COUNT,
    LTR_LABEL_GAIN,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_dataset(path: Path) -> list[dict]:
    """Load LTR dataset from JSONL."""
    dataset = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                dataset.append(json.loads(line))
    logger.info(f"Loaded {len(dataset)} rows from {path}")
    return dataset


def prepare_lgb_data(dataset: list[dict]) -> tuple[
    np.ndarray, np.ndarray, np.ndarray, list[str], list[int], list[str]
]:
    """Prepare data for LightGBM training using canonical feature contract."""
    # Feature columns (exclude ID and target columns) - USE CANONICAL CONTRACT
    feature_cols = LTR_FEATURE_NAMES

    # Map human_signal to integer labels for LightGBM ranking
    # 0 = negative, 1 = equal/neutral, 2 = positive
    signal_to_label = {0.0: 0, 0.5: 1, 1.0: 2}

    # Group by resume_id
    groups: dict[str, list[int]] = {}
    for i, row in enumerate(dataset):
        rid = row["resume_id"]
        groups.setdefault(rid, []).append(i)

    # Build arrays
    X = np.zeros((len(dataset), LTR_FEATURE_COUNT), dtype=np.float32)
    y = np.zeros(len(dataset), dtype=np.int32)
    group_sizes = []
    group_ids = []

    for _rid, indices in groups.items():
        group_sizes.append(len(indices))
        group_ids.append(_rid)
        for idx in indices:
            row = dataset[idx]
            y[idx] = signal_to_label.get(row["human_signal"], 0)
            for j, col in enumerate(feature_cols):
                val = row.get(col)
                X[idx, j] = float(val) if val is not None else 0.0

    logger.info(f"Prepared data: X={X.shape}, y={y.shape}, groups={len(group_sizes)}")
    logger.info(
        f"Group sizes: min={min(group_sizes)}, "
        f"max={max(group_sizes)}, avg={np.mean(group_sizes):.1f}"
    )
    logger.info(f"Groups: {group_ids}")

    # Label gain for NDCG: gain for label 0, 1, 2
    label_gain = LTR_LABEL_GAIN  # [0, 1, 3]

    return X, y, np.array(group_sizes), feature_cols, label_gain, group_ids


def train_lambdamart(
    X: np.ndarray,
    y: np.ndarray,
    group_sizes: np.ndarray,
    feature_names: list[str],
    label_gain: list[int],
    params: dict[str, Any] | None = None,
    use_early_stopping: bool = False,
) -> lgb.LGBMRanker:
    """Train LightGBM LambdaMART ranker."""
    default_params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [1, 3, 5, 10, 20],
        "label_gain": label_gain,
        "learning_rate": 0.05,
        "num_leaves": 63,
        "max_depth": -1,
        "min_data_in_leaf": 20,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "lambda_l1": 0.1,
        "lambda_l2": 0.1,
        "n_estimators": 500,
        "verbose": -1,
        "random_state": 42,
        "force_col_wise": True,
    }

    if use_early_stopping:
        default_params["early_stopping_rounds"] = 50

    if params:
        default_params.update(params)

    model = lgb.LGBMRanker(**default_params)

    if use_early_stopping:
        # Split internally for early stopping
        unique_groups = np.cumsum(group_sizes)
        train_group_end = unique_groups[0]

        X_train = X[:train_group_end]
        y_train = y[:train_group_end]
        X_val = X[train_group_end:]
        y_val = y[train_group_end:]
        group_train = group_sizes[:1]
        group_val = group_sizes[1:]

        model.fit(
            X_train, y_train,
            group=group_train,
            eval_set=[(X_val, y_val)],
            eval_group=[group_val],
            eval_at=[1, 3, 5, 10, 20],
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(100)]
        )
    else:
        # Train on all data
        model.fit(
            X, y,
            group=group_sizes,
            eval_at=[1, 3, 5, 10, 20],
        )

    return model


def compute_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    group_sizes: np.ndarray,
) -> dict[str, float]:
    """Compute NDCG, MRR, Recall for a single group or aggregated."""
    results = {}

    # NDCG
    ndcg_scores = {"ndcg@1": [], "ndcg@3": [], "ndcg@5": [], "ndcg@10": [], "ndcg@20": []}
    start = 0
    for g_size in group_sizes:
        end = start + g_size
        y_t = y_true[start:end].reshape(1, -1)
        y_p = y_pred[start:end].reshape(1, -1)

        for k in [1, 3, 5, 10, 20]:
            k_actual = min(k, g_size)
            if k_actual > 0:
                score = ndcg_score(y_t, y_p, k=k_actual)
                ndcg_scores[f"ndcg@{k}"].append(score)
        start = end

    for k, scores in ndcg_scores.items():
        if scores:
            results[k] = float(np.mean(scores))

    # MRR
    mrr_scores = []
    start = 0
    for g_size in group_sizes:
        end = start + g_size
        y_t = y_true[start:end]
        y_p = y_pred[start:end]

        order = np.argsort(-y_p)
        for rank, idx in enumerate(order):
            if y_t[idx] > 0:
                mrr_scores.append(1.0 / (rank + 1))
                break
        else:
            mrr_scores.append(0.0)
        start = end

    results["mrr"] = float(np.mean(mrr_scores)) if mrr_scores else 0.0

    # Recall@k
    for k in [10, 20]:
        recall_scores = []
        start = 0
        for g_size in group_sizes:
            end = start + g_size
            y_t = y_true[start:end]
            y_p = y_pred[start:end]

            top_k = min(k, g_size)
            order = np.argsort(-y_p)[:top_k]
            relevant_in_topk = sum(1 for idx in order if y_t[idx] > 0)
            total_relevant = sum(1 for v in y_t if v > 0)
            if total_relevant > 0:
                recall_scores.append(relevant_in_topk / total_relevant)
            else:
                recall_scores.append(0.0)
            start = end

        results[f"recall@{k}"] = float(np.mean(recall_scores)) if recall_scores else 0.0

    return results


def leave_one_group_out_cv(
    X: np.ndarray,
    y: np.ndarray,
    group_sizes: np.ndarray,
    group_ids: list[str],
    feature_names: list[str],
    label_gain: list[int],
) -> dict[str, Any]:
    """Leave-One-Group-Out cross-validation."""
    n_groups = len(group_sizes)
    if n_groups < 2:
        raise ValueError("Need at least 2 groups for Leave-One-Group-Out CV")

    fold_results = []

    for test_idx in range(n_groups):
        # Build train/val split by group
        train_group_mask = np.ones(n_groups, dtype=bool)
        train_group_mask[test_idx] = False

        train_groups = np.array(group_ids)[train_group_mask]
        val_groups = np.array(group_ids)[~train_group_mask]

        # Compute indices
        group_starts = np.cumsum([0] + list(group_sizes))
        train_indices = []
        val_indices = []

        for i, _g_id in enumerate(group_ids):
            start = group_starts[i]
            end = group_starts[i + 1]
            if i == test_idx:
                val_indices.extend(range(start, end))
            else:
                train_indices.extend(range(start, end))

        X_train = X[train_indices]
        y_train = y[train_indices]
        X_val = X[val_indices]
        y_val = y[val_indices]

        train_group_sizes = group_sizes[train_group_mask]
        val_group_sizes = group_sizes[~train_group_mask]

        logger.info(
            f"Fold {test_idx + 1}/{n_groups}: "
            f"train={train_groups} ({len(X_train)} samples), "
            f"val={val_groups} ({len(X_val)} samples)"
        )

        # Train model on this fold
        model = train_lambdamart(X_train, y_train, train_group_sizes, feature_names, label_gain)

        # Evaluate on validation
        val_preds = model.predict(X_val)
        val_metrics = compute_metrics(y_val, val_preds, val_group_sizes)

        # Evaluate current ranking on validation
        ce_idx = feature_names.index("ce_ettin_raw")
        current_scores = X_val[:, ce_idx]
        current_metrics = compute_metrics(y_val, current_scores, val_group_sizes)

        fold_results.append({
            "train_group": train_groups[0],
            "val_group": val_groups[0],
            "val_size": len(X_val),
            "ltr_metrics": val_metrics,
            "current_metrics": current_metrics,
        })

    return {"folds": fold_results}


def evaluate_on_all_groups(
    model: lgb.LGBMRanker,
    X: np.ndarray,
    y: np.ndarray,
    group_sizes: np.ndarray,
    group_ids: list[str],
    feature_names: list[str],
) -> dict[str, Any]:
    """Evaluate model on each group separately and aggregate."""
    group_starts = np.cumsum([0] + list(group_sizes))
    group_results = {}

    for i, g_id in enumerate(group_ids):
        start = group_starts[i]
        end = group_starts[i + 1]

        X_g = X[start:end]
        y_g = y[start:end]
        g_size = group_sizes[i]

        # LTR predictions
        preds = model.predict(X_g)
        ltr_metrics = compute_metrics(y_g, preds, np.array([g_size]))

        # Current ranking (ce_ettin_raw)
        ce_idx = feature_names.index("ce_ettin_raw")
        current_scores = X_g[:, ce_idx]
        current_metrics = compute_metrics(y_g, current_scores, np.array([g_size]))

        group_results[g_id] = {
            "ltr": ltr_metrics,
            "current": current_metrics,
            "size": int(g_size),
        }

    # Aggregate mean across groups
    metrics_keys = set()
    for g_res in group_results.values():
        metrics_keys.update(g_res["ltr"].keys())
        metrics_keys.update(g_res["current"].keys())

    aggregated = {"ltr": {}, "current": {}}
    for k in metrics_keys:
        ltr_vals = [g["ltr"].get(k, 0) for g in group_results.values() if k in g["ltr"]]
        cur_vals = [g["current"].get(k, 0) for g in group_results.values() if k in g["current"]]
        if ltr_vals:
            aggregated["ltr"][k] = float(np.mean(ltr_vals))
        if cur_vals:
            aggregated["current"][k] = float(np.mean(cur_vals))

    return {
        "per_group": group_results,
        "aggregated": aggregated,
    }


def main():
    dataset_path = Path("data/matching/ltr_dataset.jsonl")
    dataset = load_dataset(dataset_path)

    X, y, group_sizes, feature_names, label_gain, group_ids = prepare_lgb_data(dataset)

    print("=== Dataset Composition ===")
    print(f"Total rows: {len(dataset)}")
    print(f"Unique resumes (groups): {len(group_ids)}")
    print(f"Group IDs: {group_ids}")
    print(f"Group sizes: {dict(zip(group_ids, group_sizes, strict=True))}")

    # Signal distribution
    signals = [row["human_signal"] for row in dataset]
    signal_dist = {}
    for s in signals:
        signal_dist[s] = signal_dist.get(s, 0) + 1
    print(f"\nSignal distribution: {signal_dist}")

    # Positive/negative/equal counts
    pos = sum(1 for s in signals if s == 1.0)
    neg = sum(1 for s in signals if s == 0.0)
    eq = sum(1 for s in signals if s == 0.5)
    print(f"Positive (1.0): {pos}")
    print(f"Negative (0.0): {neg}")
    print(f"Equal (0.5): {eq}")

    # Run Leave-One-Group-Out CV
    print("\n=== Leave-One-Group-Out Cross-Validation ===")
    cv_results = leave_one_group_out_cv(X, y, group_sizes, group_ids, feature_names, label_gain)

    # Print fold results
    for fold in cv_results["folds"]:
        print(f"\n--- Fold: test={fold['val_group']}, train={fold['train_group']} ---")
        print(f"  Validation size: {fold['val_size']}")
        for k in ["ndcg@10", "ndcg@20", "mrr", "recall@10", "recall@20"]:
            ltr_val = fold["ltr_metrics"].get(k, 0)
            cur_val = fold["current_metrics"].get(k, 0)
            diff = ltr_val - cur_val
            print(f"  {k}: LTR={ltr_val:.4f} vs Current={cur_val:.4f} (diff={diff:+.4f})")

    # Train final model on all data
    print("\n=== Final Model (trained on all data) ===")
    final_model = train_lambdamart(X, y, group_sizes, feature_names, label_gain)

    # Evaluate on each group
    eval_results = evaluate_on_all_groups(final_model, X, y, group_sizes, group_ids, feature_names)

    for g_id, g_res in eval_results["per_group"].items():
        print(f"\n--- Group: {g_id} (n={g_res['size']}) ---")
        for k in ["ndcg@10", "ndcg@20", "mrr", "recall@10", "recall@20"]:
            ltr_val = g_res["ltr"].get(k, 0)
            cur_val = g_res["current"].get(k, 0)
            diff = ltr_val - cur_val
            print(f"  {k}: LTR={ltr_val:.4f} vs Current={cur_val:.4f} (diff={diff:+.4f})")

    # Aggregated
    print("\n=== Aggregated (mean across groups) ===")
    for k in ["ndcg@10", "ndcg@20", "mrr", "recall@10", "recall@20"]:
        ltr_val = eval_results["aggregated"]["ltr"].get(k, 0)
        cur_val = eval_results["aggregated"]["current"].get(k, 0)
        diff = ltr_val - cur_val
        print(f"  {k}: LTR={ltr_val:.4f} vs Current={cur_val:.4f} (diff={diff:+.4f})")

    # Feature importance
    print("\n--- Feature Importance (top 15) ---")
    importance = final_model.feature_importances_
    for idx in np.argsort(-importance)[:15]:
        print(f"  {feature_names[idx]}: {importance[idx]}")

    # Save model
    model_path = Path("models/lambdamart_baseline.txt")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    final_model.booster_.save_model(str(model_path))
    logger.info(f"Model saved to {model_path}")


if __name__ == "__main__":
    main()