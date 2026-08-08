# Matching v2 architecture

Read this document when changing matching stages, service boundaries, scoring, fallbacks, or the
relationship between legacy and v2 results.

The pipeline is:

```text
structured extraction -> evidence indexing -> hybrid retrieval -> reranking -> deterministic scoring
```

PostgreSQL owns requirements, candidate evidence, embedding metadata, per-requirement matches, and
aggregate results. OpenSearch contains only a rebuildable evidence projection. LLMs extract typed,
source-grounded inputs but never choose the final score.

## Components

- `app/matching/extraction.py` and `model_extractors.py` produce versioned vacancy requirements and
  candidate evidence.
- `app/matching/indexing.py` and `opensearch_index.py` own evidence projection and aliases.
- `app/matching/retrieval.py` fuses bounded lexical and dense candidates with mandatory user and
  resume filters.
- `app/matching/scoring.py` applies deterministic weights, blockers, eligibility, and score caps.
- `app/matching/pipeline.py` coordinates stages and persists the result.
- `app/matching/jobs.py` and `backfill.py` provide durable, idempotent execution.
- `ml_service/` is the optional embedding/reranking HTTP service; ordinary tests use fakes.

## Run lifecycle and idempotency

Each application run moves through `pending`, `extracting`, `indexing`, `retrieving`, `reranking`,
and `scored`. A recoverable failure may produce `degraded` when the legacy score remains usable;
otherwise it becomes `failed`.

Extraction IDs bind source content, model, model version, and schema version. Workflow keys also bind
the application, selected resume, and vacancy content so retries replace logical results rather than
creating duplicates.

## Scoring invariants

- Required requirements outweigh preferred and optional requirements.
- A hard blocker sets ineligibility and caps the score regardless of semantic similarity.
- Conceptual evidence cannot become hands-on or production evidence.
- Related evidence receives a lower capped contribution.
- Missing work authorization cannot be compensated by another skill category.
- The final 0–100 score is a pure function of versioned inputs and scoring configuration.
- A network response or model output never directly supplies the final score.

## Failure behavior

- Extraction failure uses a prior valid extraction or conservative deterministic fallback and marks
  the result degraded.
- Embedding failure falls back to lexical retrieval.
- OpenSearch failure falls back to a bounded PostgreSQL lexical scan without mutating business data.
- Reranker failure uses normalized hybrid scores with a conservative cap.
- A failed shadow calculation does not block the existing discovery/application path.

`applications.match_score` retains its legacy meaning in shadow mode. When v2 is deliberately
promoted, the deterministic final score can be synchronized to that compatibility field. Promotion
requires the reviewed evaluation and rollback process in `matching-evaluation.md` and
`matching-operations.md`.

See `matching-data-model.md` for persistence and `matching-local-development.md` for local checks.
