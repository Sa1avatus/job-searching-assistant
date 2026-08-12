# Matching v2 architecture

Read this document when changing matching stages, service boundaries, scoring, fallbacks, or the
relationship between legacy and v2 results.

The pipeline is:

```text
structured extraction -> skill normalization -> hard relevance gate -> evidence indexing -> hybrid retrieval -> reranking -> deterministic scoring -> RAG enrichment
```

PostgreSQL owns requirements, candidate evidence, embedding metadata, per-requirement matches, and
aggregate results. OpenSearch contains only a rebuildable evidence projection. LLMs extract typed,
source-grounded inputs but never choose the final score.

## Components

- `app/matching/extraction.py` and `model_extractors.py` produce versioned vacancy requirements and
  candidate evidence.
- `app/matching/normalization.py` resolves skill aliases (Postgres→postgresql, K8s→kubernetes, etc.)
  during extraction. Applied to requirement normalized_text and evidence skill_name before matching.
  Custom aliases can be injected via the constructor.
- `app/matching/relevance.py` rejects only explicit typed contradictions before indexing and model
  ranking. Missing or unknown typed evidence is reviewable and never becomes an automatic reject.
  Every aggregate explanation records the gate decision; rejected results also record stable reason
  codes and the affected requirement identifiers.
- `app/matching/indexing.py` and `opensearch_index.py` own evidence projection and aliases.
- `app/matching/retrieval.py` fuses bounded lexical and dense candidates with mandatory user and
  resume filters.
- `app/matching/rag_client.py` provides an optional RAG integration layer. `RagClient` protocol
  with `search()`, `ingest_document()`, and `health()` methods. `RagHttpClient` implements the
  actual `rag-platform` contract; `RagFallbackClient` returns empty results when RAG is unavailable.
  RAG is never a hard dependency — the pipeline works identically without it.
- `app/matching/scoring.py` applies deterministic weights, blockers, eligibility, and score caps.
  Component scores include hard_skill, preferred_skill, role, seniority, experience, work_format,
  location, domain, and language. Aggregate quality signals include semantic_similarity (weighted
  avg hybrid_score, 0–1), reranker_score (weighted avg normalized reranker score, 0–1), and
  requirements_match (required match ratio, 0–100%).
- `app/matching/pipeline.py` coordinates stages, persists the result, and optionally enriches the
  explanation with RAG context from related vacancies and profiles.
- `app/matching/jobs.py` and `backfill.py` provide durable, idempotent execution.
- `ml_service/` remains the optional embedding HTTP service. Reranking can be delegated to the
  independent sibling `reranker-service` through its bearer-authenticated public API; ordinary
  tests use contract-accurate fakes.

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
- An explicit incompatible role family or known authorization contradiction stops before indexing,
  retrieval, and reranking and persists stable reason codes in the aggregate explanation.
- Conceptual evidence cannot become hands-on or production evidence.
- Related evidence receives a lower capped contribution.
- Missing work authorization cannot be compensated by another skill category.
- The final 0–100 score is a pure function of versioned inputs and scoring configuration.
- A network response or model output never directly supplies the final score.
- RAG context enrichment is informational only — it never changes the deterministic score.

## Failure behavior

- Extraction failure uses a prior valid extraction or conservative deterministic fallback and marks
  the result degraded.
- Embedding failure falls back to lexical retrieval.
- OpenSearch failure falls back to a bounded PostgreSQL lexical scan without mutating business data.
- Reranker failure uses normalized hybrid scores with a conservative cap.
- Missing independent reranker configuration performs no network call and follows the same
  conservative hybrid-score fallback.
- RAG unavailability is logged and recorded in the explanation as an error dict; the pipeline
  continues with local data only.
- A failed shadow calculation does not block the existing discovery/application path.

`applications.match_score` retains its legacy meaning in shadow mode. When v2 is deliberately
promoted, the deterministic final score can be synchronized to that compatibility field. Promotion
requires the reviewed evaluation and rollback process in `matching-evaluation.md` and
`matching-operations.md`.

See `matching-data-model.md` for persistence and `matching-local-development.md` for local checks.
