# Matching architecture migration plan

Status: analysis complete; implementation is split into independently verifiable tasks.

## Scope and invariants

The migration replaces substring-oriented vacancy scoring with:

`structured extraction -> hybrid retrieval -> embeddings -> reranking -> deterministic scoring`

PostgreSQL remains the source of truth. OpenSearch is a disposable derived index. LLM output is
validated structured input and never determines the final score. Existing HeadHunter, LinkedIn,
Greenhouse, vacancy creation, application preparation, dashboard, and review contracts remain
backward compatible. `applications.match_score` remains populated until shadow-mode evaluation and
backfill are complete.

Non-goals for this increment are Kubernetes, MinIO, real-model downloads in ordinary tests, and
automatic replacement of the user-visible score before evaluation.

## Current architecture and data flow

1. Browser/API adapters create `VacancyRow` through `RecruitmentService.create_vacancy`.
2. Vacancy data stores `required_skills`, `preferred_skills`, `description_text`, title, company,
   location, adapter, URL, and source evidence.
3. Resume analysis stores reviewed skills, experience summary, keywords, and years on `CvFileRow`.
   Verified non-resume facts remain in `ProfileFactRow`.
4. `RecruitmentService.prepare_application` calls `app.domain.policy.assess_vacancy`, whose score is
   required-skill coverage worth 70 points plus preferred-skill coverage worth 30 points.
5. Browser discovery calls `JobDiscoveryService._rescore_from_text` after application creation.
   This second path extracts candidate skill occurrences from vacancy text and overwrites
   `ApplicationRow.match_score`.
6. Applications are unique by `(user_id, vacancy_id)`. Discovery deduplicates vacancies by source
   URL and returns outcomes sorted by the legacy score.
7. API schemas, CLI review output, saved-vacancy filters, review queue, browser tasks, and dashboard
   read the legacy integer score.

The current repository uses synchronous SQLAlchemy 2 sessions, Alembic, PostgreSQL in Compose,
SQLite metadata-created databases in most tests, Redis leases plus SQL `SKIP LOCKED` task claims,
and provider-neutral user-configured LLM clients for existing resume/material generation. There is
no current embedding, reranking, OpenSearch, Celery, RQ, or semantic matching abstraction.

## Integration points

- Vacancy ingestion: `app/services/recruitment.py`, `app/services/job_discovery.py`, and import API
  handlers in `app/api/main.py`.
- CV analysis and selection: `app/services/resume_intake.py`, `RecruitmentService.save_cv_profile`,
  `verified_profile_facts`, and the active-CV API.
- Legacy scoring: `app/domain/policy.py`, `RecruitmentService.prepare_application`, and
  `JobDiscoveryService._rescore_from_text`.
- Persistence: `app/storage/tables.py` and `migrations/versions`.
- Dispatch: `WorkflowTaskRow`, `app/workers/dispatcher.py`, and the browser-specific worker queue.
- Read contracts: assessment, discovery outcome, saved vacancies, review queue, and CLI output.

## Target component boundaries

All interfaces are provider-neutral and receive validated domain objects.

- `VacancyRequirementExtractor.extract(vacancy_id, source_text) -> VacancyExtraction`
- `CandidateEvidenceExtractor.extract(user_id, cv_file_id, source_text) -> EvidenceExtraction`
- `EmbeddingClient.embed(texts) -> EmbeddingBatch`
- `EvidenceIndex.index/delete/search` owns OpenSearch mappings and alias management.
- `HybridRetriever.retrieve(requirement, filters, limit) -> tuple[RetrievalCandidate, ...]`
- `Reranker.rerank(requirement, candidates) -> tuple[RerankedCandidate, ...]`
- `DeterministicMatchScorer.score(requirements, matches, context) -> MatchResult`
- `MatchingPipeline.match(application_id) -> MatchResult`

Production implementations will be accompanied by deterministic fakes. Domain orchestration does
not import Ollama, OpenSearch, HTTP clients, or model libraries directly.

## PostgreSQL schema

String-valued controlled vocabularies are validated in Python initially to keep migrations and
SQLite tests portable.

### `vacancy_requirements`

UUID string primary key; `vacancy_id` cascading FK; original and normalized text; requirement type;
importance; numeric weight; blocker flag; JSON alternatives; source fragment/section; extraction
model, model version, schema version, run ID, confidence; created/updated timestamps. Index
`(vacancy_id, importance, requirement_type)` and uniqueness on
`(vacancy_id, extraction_run_id, normalized_text, requirement_type)`.

### `candidate_evidence`

UUID string primary key; cascading `user_id` and `cv_file_id` FKs; original and normalized evidence;
evidence type; optional skill name, experience level, years; verified flag; source
fragment/section; extraction model/version/schema; confidence; created/updated timestamps. Index
`(cv_file_id, evidence_type, experience_level)`. Code verifies that the CV belongs to the user.

### `embedding_records`

UUID string primary key; entity type and entity ID; model name/revision; dimensions;
normalization method; content hash; index name; indexed timestamp; created timestamp. Unique
`(entity_type, entity_id, model_name, model_revision, content_hash)`. The vector itself lives in
OpenSearch; this row is the reproducibility and synchronization ledger.

### `requirement_matches`

UUID string primary key; cascading application and requirement FKs; nullable evidence FK; lexical,
dense, hybrid, reranker raw/normalized, and final scores; match level; explanation; JSON model
versions; created timestamp. Unique `(application_id, requirement_id)`, so reruns replace the
current result transactionally while version evidence remains in the parent result.

### `application_match_results`

One-to-one primary/cascading FK to application; eligibility status; final and component scores;
blocker/matched/missing counts; scoring version; JSON model versions and explanation; calculated
timestamp. `ApplicationRow.match_score` is synchronized from `final_score` only when the configured
rollout mode permits it; in shadow mode it is left unchanged.

## OpenSearch index

Use versioned physical indices such as `candidate-evidence-v1-<timestamp>` behind the
`candidate-evidence-read` and `candidate-evidence-write` aliases. A full rebuild creates a new
physical index from PostgreSQL, validates counts and samples, and atomically switches aliases.

```json
{
  "settings": {
    "index.knn": true,
    "analysis": {
      "analyzer": {
        "multilingual_text": {"type": "standard"}
      }
    }
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "evidence_id": {"type": "keyword"},
      "user_id": {"type": "keyword"},
      "cv_file_id": {"type": "keyword"},
      "evidence_text": {"type": "text", "analyzer": "multilingual_text"},
      "normalized_text": {"type": "text", "analyzer": "multilingual_text"},
      "skill_name": {"type": "keyword"},
      "evidence_type": {"type": "keyword"},
      "experience_level": {"type": "keyword"},
      "is_verified": {"type": "boolean"},
      "years": {"type": "float"},
      "confidence": {"type": "float"},
      "embedding": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "name": "hnsw",
          "space_type": "cosinesimil",
          "engine": "lucene"
        }
      },
      "embedding_model": {"type": "keyword"},
      "embedding_revision": {"type": "keyword"},
      "content_hash": {"type": "keyword"},
      "indexed_at": {"type": "date"}
    }
  }
}
```

Dimension is created from configured model metadata and must equal the embedding response; changing
the model or dimension creates a new physical index rather than mutating the mapping.

## Matching sequence

1. Persist or update a vacancy in PostgreSQL.
2. Extract versioned requirements; on provider failure, retain prior valid extraction or use a
   deterministic conservative parser and mark the run degraded.
3. Extract versioned evidence for the selected CV. Only source-grounded evidence is verified.
4. Embed changed evidence by content hash and index it; PostgreSQL records synchronization metadata.
5. For every requirement, apply user/CV metadata filters, retrieve BM25 and kNN candidates, fuse
   ranks deterministically, and retain component scores.
6. Rerank the bounded candidate set with `BAAI/bge-reranker-v2-m3`.
7. Classify evidence as exact, strong, partial, related, theoretical-only, missing, or blocker.
8. Apply deterministic weights, hard blockers, caps, and eligibility rules.
9. Store requirement matches and the aggregate result in one PostgreSQL transaction.
10. In shadow mode, keep the legacy visible score and record both values for evaluation.

### Deterministic scoring rules

- Required requirements dominate preferred and optional requirements.
- A hard blocker sets ineligible status and caps the score regardless of semantic similarity.
- Theoretical/conceptual evidence cannot receive a hands-on or production match level.
- Related evidence receives a lower capped contribution.
- Missing required work authorization cannot be compensated by another category.
- The final 0-100 score is a pure function of versioned inputs and scoring configuration.
- No network call or LLM response directly supplies the final score.

## API compatibility and additions

Existing response field `match_score` remains an integer and keeps its present meaning during
shadow mode. Additive endpoints:

- `GET /v1/applications/{application_id}/match-details`
- `POST /v1/applications/{application_id}/recalculate-match`
- operational CLI/API command for full evidence reindex
- optional admin backfill command with dry-run and bounded batches

Match details expose eligibility, final and component scores, scoring/model versions, requirements,
selected evidence, all component scores, match levels, blockers, missing requirements, and source
fragments. They never expose another user's evidence.

## Failure and fallback behavior

- Extraction unavailable: retry boundedly; then use last valid extraction or conservative
  deterministic extraction and label the result degraded.
- Embedding unavailable: use BM25 retrieval and label dense score unavailable.
- OpenSearch unavailable: use bounded PostgreSQL evidence candidates with deterministic lexical
  matching; do not lose or mutate business data.
- Reranker unavailable: use normalized hybrid score with a conservative cap.
- LLM unavailable: no fabricated extraction; use prior or deterministic fallback.
- Partial indexing failure: PostgreSQL remains authoritative and the reindex command repairs drift.
- A failed shadow calculation never blocks current discovery/application creation.

## Migration, backfill, and rollback

1. Add nullable/additive tables and configuration; retain old scoring unchanged.
2. Backfill CV evidence and vacancy requirements in bounded, idempotent batches.
3. Build the versioned OpenSearch index from PostgreSQL and validate it.
4. Run new matching in shadow mode and compare coverage, blocker precision, score distribution,
   language behavior, latency, and failure rates.
5. Enable new reads for internal match details, then optionally synchronize the legacy score.
6. Roll back by disabling the new pipeline and continuing to read `applications.match_score`.
   Additive tables and indices can remain or be removed in a later migration.

Every backfill row is keyed by extraction/model/schema version and content hash. Re-running a batch
must not duplicate logical records.

## Observability

Structured logs and metrics carry correlation, user, CV, vacancy, application, extraction run,
scoring version, model revision, and fallback mode without source CV text or secrets. Measure
extraction validation failures, indexing lag, retrieval/reranking latency, fallbacks, blockers,
legacy/new score deltas, and per-stage error counts.

## Test strategy

- Unit: schema validation, alternatives, experience levels, normalization, blockers, score caps,
  conceptual versus hands-on, fallbacks, and idempotency.
- Integration: PostgreSQL persistence, OpenSearch index/reindex, embedding/reranker contracts,
  background job, API details, and tenant filters.
- Contract: Ollama structured output, embedding service, and reranker service.
- End-to-end controlled smoke: CV extraction through stored explainable match details.
- Default tests use deterministic fake extractors, embeddings, and rerankers and never download ML
  models or access real job sites.

## Dependency graph and implementation tasks

1. **Domain tables and migration** -> no production behavior change.
2. **Typed extraction schemas and fake extractors** -> depends on 1.
3. **Ollama structured extractors and prompt versions** -> depends on 2.
4. **Embedding and reranker protocols plus deterministic fakes** -> depends on 2.
5. **OpenSearch client, mappings, aliases, and Compose service** -> depends on 1 and 4.
6. **Evidence indexing and full reindex command** -> depends on 2, 4, and 5.
7. **Hybrid retrieval and tenant/CV filters** -> depends on 5 and 6.
8. **Deterministic scorer and explanations** -> depends on 2 and 4.
9. **Matching pipeline persistence and idempotent background job** -> depends on 3, 7, and 8.
10. **Match-details API and shadow-mode metrics** -> depends on 9.
11. **Backfill tooling and evaluation fixtures** -> depends on 9 and 10.
12. **Controlled rollout and legacy score synchronization** -> depends on evaluation acceptance.
13. **Operations/development/evaluation/data-model documentation** -> evolves with each task.

Each task gets its own `tasks/current.json`, narrow file allowlist, targeted validation, diff review,
and correction task when needed. No task proceeds while its required checks fail.

## Expected file changes

Initial: `app/storage/tables.py`, a new Alembic revision, model/migration tests, this plan, and task
specifications. Later tasks add focused modules under `app/matching/`, OpenSearch/ML adapters under
`app/integrations/`, matching worker integration, API schemas/routes, Compose configuration,
scripts, tests, README, and matching-specific documentation. Existing browser adapters and their
submission behavior are not rewritten.

