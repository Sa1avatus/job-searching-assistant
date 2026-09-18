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
