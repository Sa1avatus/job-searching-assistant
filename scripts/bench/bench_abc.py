"""A/B/C offline benchmark for vacancy-relevance scoring, against real human labels for a
single resume:

    A - the production keyword heuristic (_rescore_from_text, reimplemented read-only here)
    B - multilingual-e5-small cosine similarity, served by matching-models (localhost:8090,
        scoring_mode=e5_cosine)
    C - Qwen3-Reranker-0.6B, served by the external reranker-service (localhost:8200),
        in two variants: a plain query and a query prefixed with a task instruction
    A+B, A+C - convex blends, alpha selected by 5-fold cross-validation (never on the full set)

Usage:

    python scripts/bench/bench_abc.py [--bootstrap 1000] [--folds 5]

Read-only against the database (annotation_feedback, vacancies, cv_files). Calls
matching-models and reranker-service directly over HTTP; does not touch
_rescore_from_text, app/domain/policy.py, or any other production scoring path, and makes
no writes anywhere. Every unique vacancy is scored once per variant and reused across all
pointwise/pairwise records that reference it. Calls to reranker-service are made one request
at a time (no concurrency) since that service is shared with other projects.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.services.job_discovery import _contains_skill, _normalize_skill
from app.storage.database import SessionFactory
from app.storage.tables import AnnotationFeedbackRow, CvFileRow, VacancyRow

MATCHING_MODELS_URL = "http://localhost:8090"
RERANKER_URL = "http://localhost:8200"
QWEN_INSTRUCTION = (
    "Given a candidate's resume, judge whether the job vacancy is a good fit for this candidate."
)
RANDOM_SEED = 20260925
ALPHA_GRID = [round(a * 0.05, 2) for a in range(21)]  # 0.00, 0.05, ..., 1.00
VARIANTS = ("A", "B", "C_default", "C_instructed")


# --------------------------------------------------------------------- score A: the heuristic


def old_score(vacancy: VacancyRow, cv: CvFileRow) -> float:
    """Reimplementation of app.services.job_discovery._rescore_from_text, read-only.

    Kept in lockstep with the production function by construction (same helpers, same
    formula); not imported directly because that function also performs vacancy scoring
    side effects that don't belong in an offline benchmark.
    """
    candidate_skills = {_normalize_skill(s) for s in (cv.skills or []) if s.strip()}
    required_skills = {_normalize_skill(s) for s in (vacancy.required_skills or []) if s.strip()}
    preferred_skills = {_normalize_skill(s) for s in (vacancy.preferred_skills or []) if s.strip()}
    vacancy_text = f"{vacancy.title}\n{vacancy.description_text or ''}".casefold()
    if not required_skills:
        required_skills = {s for s in candidate_skills if _contains_skill(vacancy_text, s)}
    matched_required = candidate_skills & required_skills
    matched_preferred = candidate_skills & preferred_skills
    required_coverage = len(matched_required) / len(required_skills) if required_skills else 0.0
    preferred_coverage = len(matched_preferred) / len(preferred_skills) if preferred_skills else 0.0
    title = vacancy.title.casefold()
    title_match = any(_contains_skill(title, s) for s in matched_required)
    score = required_coverage * 70 + preferred_coverage * 15 + (15 if title_match else 0)
    if required_skills and required_coverage < 0.4:
        score = min(score, 35)
    return min(100.0, round(score))


def vac_text_of(vacancy: VacancyRow) -> str:
    return f"{vacancy.title}\n{(vacancy.description_text or '')[:2000]}"


def evidence_text_of(cv: CvFileRow) -> str:
    skills_text = ", ".join(cv.skills or [])
    return f"{skills_text}\n{cv.experience_summary or ''}"[:2000]


# --------------------------------------------------------------------- score B: e5 cosine


def call_e5_cosine(client: httpx.Client, pairs: list[tuple[str, str]]) -> list[float]:
    scores: list[float] = []
    for i in range(0, len(pairs), 64):
        chunk = pairs[i : i + 64]
        response = client.post(
            f"{MATCHING_MODELS_URL}/v1/rerank",
            json={
                "scoring_mode": "e5_cosine",
                "pairs": [{"requirement": q, "evidence": d} for q, d in chunk],
            },
            timeout=60,
        )
        response.raise_for_status()
        scores.extend(s["normalized_score"] for s in response.json()["scores"])
    return scores


# --------------------------------------------------------------------- score C: Qwen reranker


def call_qwen_rerank(
    client: httpx.Client,
    api_key: str,
    query_text: str,
    documents: list[tuple[str, str]],
) -> dict[str, float]:
    """documents: list of (id, text); query is the fixed resume side (one call per <=90 docs).

    query = resume, documents = vacancies -- the reverse of the production per-claim
    reranker call (query=requirement, documents=evidence chunks). That shape doesn't fit
    here: this benchmark has one resume against many vacancies, so putting the resume in
    the query lets one call score up to 90 vacancies at once instead of one call per
    vacancy. See the benchmark report for the explicit callout.
    """
    scores: dict[str, float] = {}
    for i in range(0, len(documents), 90):
        chunk = documents[i : i + 90]
        response = client.post(
            f"{RERANKER_URL}/v1/rerank",
            json={
                "query": query_text,
                "documents": [
                    {"id": doc_id, "text": text, "metadata": {}} for doc_id, text in chunk
                ],
                "top_n": len(chunk),
                "return_documents": False,
                "truncate": True,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
        )
        response.raise_for_status()
        for result in response.json()["results"]:
            normalized = result.get("normalized_score")
            if normalized is None:
                raw = result["score"]
                normalized = 1 / (1 + pow(2.718281828, -raw))
            scores[result["id"]] = normalized
    return scores


# --------------------------------------------------------------------- data loading


@dataclass
class Dataset:
    point_labels: list[str]
    point_required_empty: list[bool]
    point_vacancy_ids: list[str]
    pair_labels: list[str]
    pair_a_ids: list[str]
    pair_b_ids: list[str]
    vacancy_scores: dict[str, dict[str, float]]  # vacancy_id -> {"A":..,"B":..,...}


def load_dataset(session) -> Dataset:
    point_rows = session.scalars(
        select(AnnotationFeedbackRow).where(AnnotationFeedbackRow.feedback_type == "pointwise")
    ).all()
    pair_rows = session.scalars(
        select(AnnotationFeedbackRow).where(AnnotationFeedbackRow.feedback_type == "pairwise")
    ).all()
    pair_rows = [r for r in pair_rows if r.label in ("a_better", "b_better")]

    all_resume_ids = {r.resume_id for r in point_rows} | {r.resume_id for r in pair_rows}
    if len(all_resume_ids) != 1:
        raise SystemExit(
            f"Expected all labels under a single resume, found {len(all_resume_ids)}: "
            f"{sorted(all_resume_ids)}. Aborting rather than silently mixing resumes."
        )
    (resume_id,) = all_resume_ids
    cv = session.get(CvFileRow, resume_id)
    if cv is None:
        raise SystemExit(f"resume {resume_id} not found")

    vacancy_ids: set[str] = set()
    for r in point_rows:
        vacancy_ids.add(r.vacancy_id)
    for r in pair_rows:
        vacancy_ids.add(r.vacancy_a_id)
        vacancy_ids.add(r.vacancy_b_id)

    vacancies: dict[str, VacancyRow] = {}
    for vid in vacancy_ids:
        vacancy = session.get(VacancyRow, vid)
        if vacancy is not None:
            vacancies[vid] = vacancy

    point_labels, point_required_empty, point_vacancy_ids = [], [], []
    for r in point_rows:
        vacancy = vacancies.get(r.vacancy_id)
        if vacancy is None or r.label not in ("relevant", "maybe", "not_relevant"):
            continue
        point_labels.append(r.label)
        point_required_empty.append(not vacancy.required_skills)
        point_vacancy_ids.append(r.vacancy_id)

    pair_labels, pair_a_ids, pair_b_ids = [], [], []
    for r in pair_rows:
        if r.vacancy_a_id not in vacancies or r.vacancy_b_id not in vacancies:
            continue
        pair_labels.append(r.label)
        pair_a_ids.append(r.vacancy_a_id)
        pair_b_ids.append(r.vacancy_b_id)

    return Dataset(
        point_labels=point_labels,
        point_required_empty=point_required_empty,
        point_vacancy_ids=point_vacancy_ids,
        pair_labels=pair_labels,
        pair_a_ids=pair_a_ids,
        pair_b_ids=pair_b_ids,
        vacancy_scores=_score_all_vacancies(vacancies, cv),
    )


def _score_all_vacancies(
    vacancies: dict[str, VacancyRow], cv: CvFileRow
) -> dict[str, dict[str, float]]:
    settings = get_settings()
    api_key = settings.reranker_api_key.get_secret_value() if settings.reranker_api_key else None
    if not api_key:
        raise SystemExit("APP_RERANKER_API_KEY is not set in .env")

    ordered_ids = list(vacancies)
    evidence_text = evidence_text_of(cv)
    vac_texts = [vac_text_of(vacancies[vid]) for vid in ordered_ids]

    print(f"Scoring {len(ordered_ids)} unique vacancies against resume {cv.id}...")

    a_scores = [old_score(vacancies[vid], cv) for vid in ordered_ids]

    with httpx.Client() as client:
        b_scores = call_e5_cosine(client, [(text, evidence_text) for text in vac_texts])

        documents = list(zip(ordered_ids, vac_texts, strict=True))
        c_default = call_qwen_rerank(client, api_key, evidence_text, documents)
        instructed_query = f"Instruct: {QWEN_INSTRUCTION}\nQuery: {evidence_text}"
        c_instructed = call_qwen_rerank(client, api_key, instructed_query, documents)

    def normalize(values: list[float]) -> list[float]:
        lo, hi = min(values), max(values)
        if hi == lo:
            return [0.5 for _ in values]
        return [(v - lo) / (hi - lo) for v in values]

    a_norm = normalize(a_scores)
    b_norm = normalize(b_scores)
    c_default_norm = normalize([c_default[vid] for vid in ordered_ids])
    c_instructed_norm = normalize([c_instructed[vid] for vid in ordered_ids])

    return {
        vid: {
            "A": a_norm[i],
            "B": b_norm[i],
            "C_default": c_default_norm[i],
            "C_instructed": c_instructed_norm[i],
        }
        for i, vid in enumerate(ordered_ids)
    }


# --------------------------------------------------------------------- metrics


def separation_metric(indices: list[int], labels: list[str], scores: list[float]) -> float | None:
    pos = [scores[i] for i in indices if labels[i] == "relevant"]
    neg = [scores[i] for i in indices if labels[i] == "not_relevant"]
    if not pos or not neg:
        return None
    wins = sum(1 for a in pos for b in neg if a > b)
    ties = sum(1 for a in pos for b in neg if a == b)
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def pairwise_metric(
    indices: list[int], labels: list[str], scores_a: list[float], scores_b: list[float]
) -> float:
    correct = 0
    for i in indices:
        sa, sb = scores_a[i], scores_b[i]
        pick = "a_better" if sa > sb else ("b_better" if sb > sa else "tie")
        if pick == labels[i]:
            correct += 1
    return correct / len(indices)


def bootstrap_separation(
    labels: list[str], scores: list[float], n_boot: int, seed: int
) -> tuple[float, float, float]:
    rng = random.Random(seed)
    n = len(labels)
    values = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        v = separation_metric(idx, labels, scores)
        if v is not None:
            values.append(v)
    return _summarize(values)


def bootstrap_pairwise(
    labels: list[str], scores_a: list[float], scores_b: list[float], n_boot: int, seed: int
) -> tuple[float, float, float]:
    rng = random.Random(seed)
    n = len(labels)
    values = [
        pairwise_metric([rng.randrange(n) for _ in range(n)], labels, scores_a, scores_b)
        for _ in range(n_boot)
    ]
    return _summarize(values)


def _summarize(values: list[float]) -> tuple[float, float, float]:
    values = sorted(values)
    mean = statistics.mean(values)
    lo = values[int(0.025 * len(values))]
    hi = values[min(int(0.975 * len(values)), len(values) - 1)]
    return round(mean, 4), round(lo, 4), round(hi, 4)


def paired_bootstrap_separation(
    labels: list[str], scores_ref: list[float], scores_x: list[float], n_boot: int, seed: int
) -> tuple[float, float, float, bool]:
    rng = random.Random(seed)
    n = len(labels)
    diffs = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        v_ref = separation_metric(idx, labels, scores_ref)
        v_x = separation_metric(idx, labels, scores_x)
        if v_ref is not None and v_x is not None:
            diffs.append(v_x - v_ref)
    return _paired_summary(diffs)


def paired_bootstrap_pairwise(
    labels: list[str],
    ref_a: list[float],
    ref_b: list[float],
    x_a: list[float],
    x_b: list[float],
    n_boot: int,
    seed: int,
) -> tuple[float, float, float, bool]:
    rng = random.Random(seed)
    n = len(labels)
    diffs = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        v_ref = pairwise_metric(idx, labels, ref_a, ref_b)
        v_x = pairwise_metric(idx, labels, x_a, x_b)
        diffs.append(v_x - v_ref)
    return _paired_summary(diffs)


def _paired_summary(diffs: list[float]) -> tuple[float, float, float, bool]:
    diffs = sorted(diffs)
    mean = statistics.mean(diffs)
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[min(int(0.975 * len(diffs)), len(diffs) - 1)]
    significant = not (lo <= 0 <= hi)
    return round(mean, 4), round(lo, 4), round(hi, 4), significant


# --------------------------------------------------------------------- CV blend alpha


def cv_alpha_separation(
    labels: list[str], score_a: list[float], score_x: list[float], folds: int, seed: int
) -> tuple[list[float], float]:
    rng = random.Random(seed)
    n = len(labels)
    order = list(range(n))
    rng.shuffle(order)
    fold_idx = [order[i::folds] for i in range(folds)]
    chosen_alphas: list[float] = []
    held_out: list[float] = []
    for k in range(folds):
        test_idx = fold_idx[k]
        train_idx = [i for j, f in enumerate(fold_idx) if j != k for i in f]
        best_alpha, best_val = 0.5, -1.0
        for alpha in ALPHA_GRID:
            blended = [alpha * score_a[i] + (1 - alpha) * score_x[i] for i in range(n)]
            val = separation_metric(train_idx, labels, blended)
            if val is not None and val > best_val:
                best_val, best_alpha = val, alpha
        chosen_alphas.append(best_alpha)
        blended_full = [best_alpha * score_a[i] + (1 - best_alpha) * score_x[i] for i in range(n)]
        test_val = separation_metric(test_idx, labels, blended_full)
        if test_val is not None:
            held_out.append(test_val)
    return chosen_alphas, round(statistics.mean(held_out), 4) if held_out else float("nan")


def cv_alpha_pairwise(
    labels: list[str],
    score_a_a: list[float],
    score_a_b: list[float],
    score_x_a: list[float],
    score_x_b: list[float],
    folds: int,
    seed: int,
) -> tuple[list[float], float]:
    rng = random.Random(seed)
    n = len(labels)
    order = list(range(n))
    rng.shuffle(order)
    fold_idx = [order[i::folds] for i in range(folds)]
    chosen_alphas: list[float] = []
    held_out: list[float] = []
    for k in range(folds):
        test_idx = fold_idx[k]
        train_idx = [i for j, f in enumerate(fold_idx) if j != k for i in f]
        best_alpha, best_val = 0.5, -1.0
        for alpha in ALPHA_GRID:
            blended_a = [alpha * score_a_a[i] + (1 - alpha) * score_x_a[i] for i in range(n)]
            blended_b = [alpha * score_a_b[i] + (1 - alpha) * score_x_b[i] for i in range(n)]
            val = pairwise_metric(train_idx, labels, blended_a, blended_b)
            if val > best_val:
                best_val, best_alpha = val, alpha
        chosen_alphas.append(best_alpha)
        blended_a = [best_alpha * score_a_a[i] + (1 - best_alpha) * score_x_a[i] for i in range(n)]
        blended_b = [best_alpha * score_a_b[i] + (1 - best_alpha) * score_x_b[i] for i in range(n)]
        held_out.append(pairwise_metric(test_idx, labels, blended_a, blended_b))
    return chosen_alphas, round(statistics.mean(held_out), 4)


# --------------------------------------------------------------------- latency


def measure_latency(dataset: Dataset, api_key: str) -> dict[str, dict[str, float]]:
    vac_ids = list(dataset.vacancy_scores)[:30]
    if not vac_ids:
        return {}
    with SessionFactory() as session:
        cv_id = session.scalars(
            select(AnnotationFeedbackRow.resume_id).where(
                AnnotationFeedbackRow.feedback_type == "pointwise"
            )
        ).first()
        cv = session.get(CvFileRow, cv_id) if cv_id else None
        vacancies = {vid: session.get(VacancyRow, vid) for vid in vac_ids}
    if cv is None or any(v is None for v in vacancies.values()):
        return {}
    evidence_text = evidence_text_of(cv)
    vac_texts = [vac_text_of(vacancies[vid]) for vid in vac_ids]

    results: dict[str, dict[str, float]] = {}
    with httpx.Client() as client:
        # B, cross_encoder mode (current production default) -- single-pair calls
        durations = []
        for text in vac_texts:
            start = time.monotonic()
            client.post(
                f"{MATCHING_MODELS_URL}/v1/rerank",
                json={"pairs": [{"requirement": text, "evidence": evidence_text}]},
                timeout=30,
            )
            durations.append(time.monotonic() - start)
        results["B_cross_encoder_no_cache"] = _latency_stats(durations)

        # B, e5_cosine, single-pair calls (no within-request dedup possible)
        durations = []
        for text in vac_texts:
            start = time.monotonic()
            client.post(
                f"{MATCHING_MODELS_URL}/v1/rerank",
                json={
                    "scoring_mode": "e5_cosine",
                    "pairs": [{"requirement": text, "evidence": evidence_text}],
                },
                timeout=30,
            )
            durations.append(time.monotonic() - start)
        results["B_e5_cosine_no_cache"] = _latency_stats(durations)

        # B, e5_cosine, one batched call for all pairs sharing the same evidence text --
        # the embedding dedup means "evidence" is embedded once, not len(vac_texts) times.
        start = time.monotonic()
        client.post(
            f"{MATCHING_MODELS_URL}/v1/rerank",
            json={
                "scoring_mode": "e5_cosine",
                "pairs": [{"requirement": text, "evidence": evidence_text} for text in vac_texts],
            },
            timeout=30,
        )
        total = time.monotonic() - start
        results["B_e5_cosine_batched_with_cache"] = {
            "p50": round(total / len(vac_texts), 4),
            "p95": round(total / len(vac_texts), 4),
            "note": "single batched call, time/pair averaged",
        }

        # C, Qwen, single-document-per-call (mirrors the production reranker call shape)
        durations = []
        for text in vac_texts:
            start = time.monotonic()
            client.post(
                f"{RERANKER_URL}/v1/rerank",
                json={
                    "query": text,
                    "documents": [{"id": "x", "text": evidence_text, "metadata": {}}],
                    "top_n": 1,
                    "return_documents": False,
                    "truncate": True,
                },
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            durations.append(time.monotonic() - start)
        results["C_qwen_single_document"] = _latency_stats(durations)

    return results


def _latency_stats(durations: list[float]) -> dict[str, float]:
    durations = sorted(durations)
    n = len(durations)
    p50 = durations[int(0.5 * n)] if n else float("nan")
    p95 = durations[min(int(0.95 * n), n - 1)] if n else float("nan")
    return {"p50": round(p50, 4), "p95": round(p95, 4)}


# --------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--skip-latency", action="store_true")
    args = parser.parse_args(argv)

    with SessionFactory() as session:
        dataset = load_dataset(session)

    n_point = len(dataset.point_labels)
    n_pair = len(dataset.pair_labels)
    print(f"\nPointwise records: {n_point}  Pairwise records: {n_pair}\n")

    point_scores = {
        v: [dataset.vacancy_scores[vid][v] for vid in dataset.point_vacancy_ids] for v in VARIANTS
    }
    pair_scores_a = {
        v: [dataset.vacancy_scores[vid][v] for vid in dataset.pair_a_ids] for v in VARIANTS
    }
    pair_scores_b = {
        v: [dataset.vacancy_scores[vid][v] for vid in dataset.pair_b_ids] for v in VARIANTS
    }

    print("=== 1. ROC-AUC-style separation (relevant vs not_relevant), pointwise ===")
    for v in VARIANTS:
        point = separation_metric(list(range(n_point)), dataset.point_labels, point_scores[v])
        mean, lo, hi = bootstrap_separation(
            dataset.point_labels, point_scores[v], args.bootstrap, RANDOM_SEED
        )
        print(f"  {v}: point={point} bootstrap_mean={mean} 95%CI=[{lo}, {hi}]")
        if v != "A":
            d_mean, d_lo, d_hi, sig = paired_bootstrap_separation(
                dataset.point_labels,
                point_scores["A"],
                point_scores[v],
                args.bootstrap,
                RANDOM_SEED,
            )
            print(
                f"    vs A: diff_mean={d_mean} 95%CI=[{d_lo}, {d_hi}] "
                f"significant={'YES' if sig else 'no'}"
            )

    print("\n=== 2. Same, on vacancies with EMPTY required_skills only ===")
    empty_idx = [i for i, e in enumerate(dataset.point_required_empty) if e]
    print(f"  n={len(empty_idx)}")
    for v in VARIANTS:
        val = separation_metric(empty_idx, dataset.point_labels, point_scores[v])
        print(f"  {v}: {val}")

    print("\n=== 3. Pairwise accuracy (agrees with human a_better/b_better) ===")
    for v in VARIANTS:
        point = pairwise_metric(
            list(range(n_pair)), dataset.pair_labels, pair_scores_a[v], pair_scores_b[v]
        )
        mean, lo, hi = bootstrap_pairwise(
            dataset.pair_labels, pair_scores_a[v], pair_scores_b[v], args.bootstrap, RANDOM_SEED
        )
        print(f"  {v}: point={round(point, 4)} bootstrap_mean={mean} 95%CI=[{lo}, {hi}]")
        if v != "A":
            d_mean, d_lo, d_hi, sig = paired_bootstrap_pairwise(
                dataset.pair_labels,
                pair_scores_a["A"],
                pair_scores_b["A"],
                pair_scores_a[v],
                pair_scores_b[v],
                args.bootstrap,
                RANDOM_SEED,
            )
            print(
                f"    vs A: diff_mean={d_mean} 95%CI=[{d_lo}, {d_hi}] "
                f"significant={'YES' if sig else 'no'}"
            )

    print("\n=== 4. Blends A+B and A+C (alpha via 5-fold CV, held-out fold reported) ===")
    for v in ("B", "C_default", "C_instructed"):
        alphas, sep_cv = cv_alpha_separation(
            dataset.point_labels, point_scores["A"], point_scores[v], args.folds, RANDOM_SEED
        )
        print(
            f"  A+{v} (pointwise separation): per-fold alpha={alphas} "
            f"mean_alpha={round(statistics.mean(alphas), 3)} held_out_separation={sep_cv}"
        )
        alphas_p, acc_cv = cv_alpha_pairwise(
            dataset.pair_labels,
            pair_scores_a["A"],
            pair_scores_b["A"],
            pair_scores_a[v],
            pair_scores_b[v],
            args.folds,
            RANDOM_SEED,
        )
        print(
            f"  A+{v} (pairwise accuracy):    per-fold alpha={alphas_p} "
            f"mean_alpha={round(statistics.mean(alphas_p), 3)} held_out_accuracy={acc_cv}"
        )

    if not args.skip_latency:
        print("\n=== 5. Latency (p50/p95 seconds per call) ===")
        settings = get_settings()
        api_key = settings.reranker_api_key.get_secret_value()
        latency = measure_latency(dataset, api_key)
        for name, stats in latency.items():
            print(f"  {name}: {stats}")

    print(
        "\nNote: all labels are under a single resume; generalization to other resumes "
        "was not evaluated."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
