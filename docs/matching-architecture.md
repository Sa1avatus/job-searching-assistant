# Matching v2 architecture

Read this document when changing matching stages, service boundaries, scoring, fallbacks, or the
relationship between legacy and v2 results.

The pipeline is:

```text
structured extraction -> skill normalization -> hard relevance gate -> evidence indexing ->
  claim decomposition -> per-claim retrieval -> reranking -> entailment evaluation ->
  deterministic scoring -> gap analysis -> RAG enrichment
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
- `app/matching/indexing.py` and `opensearch_index.py` own evidence projection and aliases.
- `app/matching/retrieval.py` fuses bounded lexical and dense candidates with mandatory user and
  resume filters.
- `app/matching/claims.py` defines atomic claim decomposition models and the decomposer protocol.
- `app/matching/claim_pipeline.py` orchestrates the claim-based matching pipeline:
  requirement decomposition → per-claim retrieval → reranking → entailment evaluation → aggregation.
- `app/matching/entailment.py` defines evidence entailment evaluation with the five-level relation
  model (entailed, partial, related_but_insufficient, contradicted, unknown) and evidence strength
  computation.
- `app/matching/duration.py` provides deterministic duration evaluation using union intervals to
  avoid double-counting parallel work.
- `app/matching/gap_analysis.py` produces profile gap analysis distinguishing skill gaps from
  evidence gaps, duration gaps, and metadata gaps.
- `app/matching/model_evaluators.py` contains LLM-backed requirement decomposer and evidence
  evaluator implementations using the existing ModelRouter.
- `app/matching/rag_client.py` provides an optional RAG integration layer. Every retrieval and
  ingestion request carries the application's owning user in `X-Owner-User-Id`; document metadata
  is not treated as an authorization boundary.
- `app/matching/rag_collections.py` defines the cross-service collection contract: `profiles` for
  reviewed facts, `resumes` for analysed CV content, and `vacancies` for vacancy context. These
  collections remain owner-scoped and must be provisioned and authorized in the RAG service.
- RAG ingestion is an owner-scoped upsert: a new logical document uses `POST`; an existing document
  is located within its authorized collection and updated with `PATCH` plus `lock_version`.
  Resume deletion and an empty verified profile propagate through the same owner boundary.
- Optimistic-lock conflicts are retried at most three times. Existing records can be synchronized
  in repeatable batches with the owner-scoped `rag-backfill` CLI command and returned cursors.
- User deletion snapshots the user's known resume and vacancy IDs before PostgreSQL deletion, then
  attempts fail-open owner-scoped removal across all three RAG collections.
- Synchronization and deletion publish content-free aggregate counters through `/metrics`.
  Backfill results separate indexed, skipped, and failed records and return independent cursors.
- `matching-backfill` scans one owner's applications in bounded ID-ordered pages and schedules only
  stale, failed, or pre-source-v2 aggregates. Failure reports contain exception class codes only.
- `app/matching/scoring.py` applies deterministic weights, blockers, eligibility, and score caps.
  Component scores include hard_skill, preferred_skill, role, seniority, experience, work_format,
  location, domain, and language. Aggregate quality signals include semantic_similarity (weighted
  avg hybrid_score, 0–1), reranker_score (weighted avg normalized reranker score, 0–1), and
  requirements_match (required match ratio, 0–100%).
- `app/matching/pipeline.py` coordinates stages, persists the result, and optionally enriches the
  explanation with RAG context from related vacancies and profiles.
- `app/matching/vacancy_source.py` builds the versioned extraction source from the vacancy title,
  description, and structured required/preferred skills. Changing a structured skill therefore
  invalidates the matching job and extraction version even when the description is unchanged.
- `app/matching/jobs.py` and `backfill.py` provide durable, idempotent execution.

## Claim-based matching pipeline

When both `RequirementDecomposer` and `EvidenceEvaluator` are injected, the pipeline uses the
claim-based path:

1. **Decomposition**: Each vacancy requirement is decomposed into atomic claims with types like
   skill, experience_duration, practical_experience, production_experience, technology, etc.
   Logical relationships (AND, OR) and criticality levels (required, preferred, bonus, hard_blocker)
   are preserved.

2. **Per-claim retrieval**: Each atomic claim gets its own targeted retrieval query, enabling
   more focused evidence search than the whole-requirement text.

3. **Entailment evaluation**: Retrieved evidence is evaluated against the claim using an LLM
   that answers "does this evidence prove this claim?" — NOT "are these texts similar?".
   Five relation levels: entailed, partial, related_but_insufficient, contradicted, unknown.

4. **Evidence strength**: Computed from semantic score (25%), reranker score (25%), and entailment
   score (50%). Capped by relation level: related_but_insufficient ≤ 0.49, etc.

5. **Aggregation**: Claim results are aggregated into requirement-level assessments using AND
   semantics (default). All required claims must be entailed for full match.

6. **Gap analysis**: After matching, identifies real skill gaps vs evidence gaps where experience
   exists but isn't sufficiently documented.

When decomposer/evaluator are not available, falls back to the legacy whole-requirement path.

## Run lifecycle and idempotency

Each application run moves through `pending`, `extracting`, `indexing`, `retrieving`, `reranking`,
and `scored`. A recoverable failure may produce `degraded` when the legacy score remains usable;
otherwise it becomes `failed`.

Extraction IDs bind source content, model, model version, and schema version. Workflow keys also bind
the application, selected resume, and vacancy content so retries replace logical results rather than
creating duplicates.

## Scoring invariants (matching-v2.3)

- Required requirements outweigh preferred and optional requirements.
- Only **confirmed** hard blockers (work authorization, mandatory license, mandatory location) set
  score to 0. Python, RAG, ML experience, Docker — even when required — are NOT hard blockers.
- Unresolved blockers (insufficient evidence for a blocker requirement) produce
  NEEDS_CONFIRMATION eligibility, not INELIGIBLE.
- Regular required requirements use proportional penalty: missing N out of M caps score at
  max(20, 80 * (1 - N/M)), NOT a fixed 49.
- Entailment relation overrides legacy match level when available.
- Semantic similarity alone is never sufficient evidence for a match.
- Related_but_insufficient evidence is capped at strength 0.49.
- evaluation_error and insufficient_evidence get residual credit (0.10 and 0.15 match factor),
  not 0.0 like missing.
- The final score includes separate required_score, preferred_score, bonus_score, confidence,
  hard_blockers, and hard_blockers_unresolved.
- RAG context enrichment is informational only — it never changes the deterministic score.
- When RAG is enabled, owner-scoped profile retrieval may refine the ordering of locally verified
  evidence before the configured reranker runs. Missing or degraded RAG preserves the local hybrid
  ordering; RAG text never becomes candidate evidence by itself.
- Confidence is penalized for each evaluation_error or unknown claim (up to -0.30).

## Failure behavior

- Extraction failure uses a prior valid extraction or conservative deterministic fallback and marks
  the result degraded.
- Embedding failure falls back to lexical retrieval.
- OpenSearch failure falls back to a bounded PostgreSQL lexical scan without mutating business data.
- Reranker failure uses normalized hybrid scores with a conservative cap.
- Claim decomposition failure falls back to legacy whole-requirement matching.
- Entailment evaluation failure marks the claim as `evaluation_error` (not unknown/missing).
  This prevents technical failures from being interpreted as missing skills.
- Duration claims with missing dates produce `insufficient_evidence`, not `missing`.
- Missing independent reranker configuration performs no network call and follows the same
  conservative hybrid-score fallback.
- RAG unavailability is logged and recorded in the explanation as an error dict; the pipeline
  continues with local data only.

`applications.match_score` retains its legacy meaning in shadow mode. When v2 is deliberately
promoted, the deterministic final score can be synchronized to that compatibility field.

## Entailment relation model (v2.3)

| Relation | Meaning | Strength cap | Match factor |
|---|---|---|---|
| entailed | Evidence explicitly/logically establishes the claim | 1.0 | 1.0 |
| partial | Evidence establishes part of the claim | 0.74 | 0.65 |
| related_but_insufficient | Evidence is topically related but does not prove the claim | 0.49 | 0.15 |
| insufficient_evidence | Available data is insufficient to determine (e.g. missing dates) | 0.0 | 0.15 |
| contradicted | Explicit contradiction exists | 0.0 | 0.0 |
| evaluation_error | Technical evaluator failure (timeout, invalid JSON, provider error) | 0.0 | 0.10 |
| unknown | Legacy fallback (should be phased out) | 0.0 | 0.0 |

Evaluator failures are stored as `evaluation_error` with sanitized failure codes in requirement
match metadata. Provider response bodies are not copied into persisted explanations. These
requirements put the application into review, but do not increase the missing-required count. A
user can explicitly retry failed evaluations from the detailed result; execution remains bounded by
the durable worker retry policy.

## Hard blocker classification

Hard blockers are reserved for truly binary eligibility conditions:
- Mandatory work authorization
- Mandatory citizenship
- Mandatory security clearance
- Mandatory professional license
- Strict mandatory language level
- Explicit mandatory location constraint

Technical skills (Python, RAG, ML, Docker, FastAPI, LLM) are NEVER hard blockers, even when
marked as required. The decomposition prompt explicitly guides the LLM on this distinction.

## Claim evaluator routing

Duration claims (`experience_duration` type) are routed to the deterministic `DurationEvaluator`
which uses union intervals — overlapping experience periods are merged, not summed. The LLM
evaluator is only used for skill, practical_experience, production_experience, technology, and
domain claims.

## Fact ingestion integration

Matching v2 uses the Facts DB as its primary evidence source. Facts can be:
- Manually entered (verified=true, trust=1.0)
- Extracted from resume via LLM (verified=false, trust=0.85)
- Imported from files (verified=false, trust varies)
- Inferred (trust ≤ 0.5)

Resume-extracted facts are automatically available as evidence for claim evaluation after
extraction. Matching results are invalidated when facts change.

See `matching-data-model.md` for persistence and `matching-local-development.md` for local checks.
