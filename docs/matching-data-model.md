# Matching v2 data model

Read this document when changing matching tables, version fingerprints, OpenSearch mappings, or
result persistence. General migration rules are in `database.md`.

- `vacancy_requirements`: versioned atomic requirements with importance, blocker status, weight,
  alternatives, confidence, and an exact source fragment.
- `candidate_evidence`: versioned CV evidence with experience level, verification state, confidence,
  and an exact source fragment.
- `embedding_records`: PostgreSQL metadata for each indexed evidence vector. Vector values live only
  in the rebuildable OpenSearch projection.
- `requirement_matches`: lexical, dense, hybrid, raw reranker, normalized reranker, final
  requirement score, explanation, and model provenance.
- `application_match_results`: aggregate component scores, eligibility, counters, run status,
  failure/fallback diagnostics, model versions, scoring version, and timestamps. Component scores
  include hard/preferred skills, role, seniority, experience, work format, location, domain, and
  language. Aggregate quality signals include weighted-average semantic similarity (0–1),
  weighted-average reranker score (0–1), and requirements match ratio (0–100%). The reranker
  aggregate is 0 when no reranker scores are available.

`applications.match_score` remains intact during migration. In shadow mode it remains the
user-visible legacy score. When v2 becomes primary, the deterministic final score is synchronized
back to that field for frontend compatibility.

Extraction run IDs are deterministic over entity ID, source hash, model, model version, and schema
version. Workflow task keys additionally bind an application to the selected CV and vacancy content,
so retries do not create duplicate business rows.

The OpenSearch projection uses versioned physical indices behind configured read/write aliases.
Documents always carry `user_id`, `cv_file_id`, evidence identity, source text, controlled evidence
metadata, embedding model/revision, content hash, and index timestamp. Mapping dimensions must match
the configured model response. Changing model meaning or dimensions requires a new physical index;
do not mutate an active vector mapping in place.

Source fragments may contain resume or vacancy text. Do not log them, place them in metrics, expose
them across users, or copy them into Worker task context unless the task explicitly needs that exact
fixture and it contains no private data.

## Human annotation dataset (`annotation_feedback`)

Human labels are the only ground truth for learning-to-rank. LLM output and application status are
never labels. Rules enforced by `app/domain/annotation.py`, the service in
`app/matching/cross_encoder/annotation.py`, and the database (migration 0043):

- **One judgement, once.** Pointwise is unique per (user, resume, vacancy); pairwise per (user,
  resume, canonical pair) - partial unique indexes `uq_annotation_pointwise` /
  `uq_annotation_pairwise`. Resubmitting updates; a concurrent double submit loses the INSERT race
  inside a SAVEPOINT and updates the winner's row.
- **Pairs are order-independent.** A pair is stored smaller vacancy id first (`pair_key =
  "<first>:<second>"`); a reversed submission flips `a_better`/`b_better` and swaps the reasons, so
  every reason stays attached to the vacancy it was written about.
- **Undecided answers are not pairs.** `both_equal` and `neither` are counted but never exported as
  (winner, loser) training pairs.
- **Ownership.** The resume must belong to the labelling user, otherwise 403 and nothing is written.
- **Validation.** Labels come from fixed vocabularies; reasons are short snake_case tags (max 10);
  `confidence` is `low|medium|high`; errors are `{code, message}`.
- **Provenance.** `annotator_id`, `source`, `confidence`, `created_at/updated_at`, and the queue
  context at labelling time (`sampling_reason`, ranks, scores).
- **Review queue.** Union of strata (top by current ranking, top by LTR, largest rank
  disagreement, mid-ranking, random) with deterministic ordering (ties broken by vacancy id). Each
  item has `review_priority` in [0, 1] and `priority_reasons`; it is a **heuristic** ordering signal,
  not a probability. Current and LTR scores are converted to percentiles on the same pool before
  disagreement is measured, and no company may take more than 30 % of the queue.
- **Export.** `GET /v1/annotation/export` returns validated pointwise rows (with ranking gain) and
  decisive pairs plus a list of rejected rows (`ownership_mismatch`, `bad_timestamps`,
  `non_canonical_pair`, `duplicate`, ...) and a reproducible `dataset_hash`.

### Building the first human dataset (200-300 labels)

The dashboard panel **Разметка** (`?panel=annotation`) drives the workflow: pointwise labelling
from the review queue, head-to-head pairs from `GET /v1/annotation/pair-queue` (near-equal scores,
or pairs the current and LTR rankings order oppositely; already-judged pairs in either order are
skipped, no vacancy appears more than twice), reason tags and confidence. System ranks and scores are
deliberately not shown to the reviewer; they are stored with the label as sampling context.

- **Leakage-safe folds** (`app/domain/annotation_dataset.py`): folds are assigned to *groups*, not
  rows - all vacancies of one company are one group, vacancies compared head to head are merged, and
  a group's fold is a hash of its id (seeded, order-independent). A group never straddles folds, so
  reposts and compared vacancies cannot appear in both train and test.
- **Frozen evaluation split** (`annotation_splits`, migration 0044): `POST /v1/annotation/splits`
  creates a named split, `POST /v1/annotation/splits/{name}/freeze` records the validation/test
  vacancies once and is irreversible. Afterwards any group touching a frozen vacancy is pinned to its
  evaluation fold (test outranks validation) and can never enter train, however many labels are
  added, so retraining cannot see the frozen data.
- **Reproducible export**: `GET /v1/annotation/export/{split}?fold=train|validation|test` returns
  rows tagged with their fold plus the validation issues and a `dataset_hash`.
- **Coverage report**: `GET /v1/annotation/dataset-report` gives meaningful labels vs the 200-label
  minimum, pointwise/pair counts, class balance, hard negatives (rated top-20 by the system, rejected
  by the human), model-disagreement cases, resume/vacancy/company diversity and warnings (single
  resume, no frozen split, class imbalance, one company dominating). `ready` is true only when no
  warning remains.

Collecting the labels is human work: nothing here generates labels, and LTR training (stage 3) must
not start before `ready` is true.

## Learning to rank (shadow only)

Code: `app/matching/ltr/` (metrics, rankers, benchmark, artifact schema) and
`app/services/ltr_training.py`; CLI: `scripts/ltr_pipeline.py`. **Production ranking is unchanged**:
`APP_LTR_ENABLED` defaults to false and even when enabled LTR only fills the shadow columns
(`ltr_score`, `ltr_status='shadow'`, `ltr_rank`, `rank_delta`, `ltr_topk_overlap`) of
`application_match_results`.

- **Gate.** Training needs a frozen split (see the annotation dataset section) and a dataset the
  coverage report calls `ready`. `--allow-incomplete` produces a *provisional* artifact that inference
  refuses (`APP_LTR_ALLOW_PROVISIONAL=true` overrides, for experiments only).
- **Leakage.** Fit on the `train` fold, report on the frozen `validation` fold; the `test` fold is
  scored only with `--final-test` and the artifact records that it was.
- **Feature schema.** `FEATURE_SCHEMA_VERSION` plus a hash of the ordered feature names is stored in
  every artifact; a mismatch refuses to load instead of scoring with shifted columns. Missing
  features stay `None` (imputed with the train mean), never a silent 0.
- **Models.** `logistic_regression` (pure Python, soft graded targets) is the baseline; `lambdamart`
  (LightGBM, optional `ltr` extra) is benchmarked when installed. Only the logistic artifact is
  wired to shadow scoring so far.
- **Benchmark.** NDCG@10, Recall@10, MRR, Precision@10 per ranker against the current pipeline's
  score on the same frozen fold. A challenger is `challenger_beats_baseline` only with a positive
  NDCG@10 lead on at least 2 groups / 50 items; otherwise `insufficient_evidence` or
  `baseline_holds`. Undefined metrics (a group with no relevant item) are None, not 0.

No model has been trained on real data yet: there are no human labels (see the annotation section),
so there is no benchmark result and the production default stays as it is.
