# Changelog

## 1.4.0 — 2026-08-13

### Matching v2 claim pipeline (v2.2 → v2.3)

- Decompose vacancy requirements into atomic claims with AND/OR semantics, criticality levels
  (required, preferred, bonus, hard_blocker), and structured claim types.
- Per-claim retrieval instead of whole-requirement text, enabling targeted evidence search.
- Evidence entailment evaluation with seven relation levels: entailed, partial,
  related_but_insufficient, insufficient_evidence, contradicted, evaluation_error, unknown.
- Evaluator prompt now provides structured claim context (subject, criticality, source requirement)
  and explicit anti-hallucination rules (no RAG from LLM integration, no production from ML training).
- Technical evaluator failures (timeout, invalid JSON, provider error) return `evaluation_error`
  instead of `unknown`, preventing false zero scores.
- Deterministic duration evaluator with union intervals — overlapping experience is merged, not
  summed. Missing dates produce `insufficient_evidence`, not `missing`.
- Fixed UNKNOWN → MISSING cascade: `unknown` and `evaluation_error` no longer map to MatchLevel.MISSING.
- Only confirmed hard blockers (work authorization, mandatory license) zero the score. Python, RAG,
  ML experience are classified as `required`, not `hard_blocker`.
- Unresolved blockers (insufficient evidence for a blocker requirement) produce
  `NEEDS_CONFIRMATION` eligibility instead of `INELIGIBLE`.
- New scoring fields: required_score, preferred_score, bonus_score, hard_blockers,
  hard_blockers_unresolved, confidence.
- Scoring version bumped to matching-v2.3.
- Cache for decomposition and entailment results with content-based invalidation.
- Configurable retrieval_top_k and reranker_top_k via APP_MATCHING_RETRIEVAL_TOP_K /
  APP_MATCHING_RERANKER_TOP_K.
- Added DB migration 0031 for is_unresolved_blocker column on requirement_matches.

### Fact ingestion from files and resume extraction

- Upload facts from TXT, MD, CSV, JSON, PDF, DOCX files via POST /v1/users/{user_id}/facts/import.
- Extract facts from existing resume via POST /v1/users/{user_id}/facts/extract-from-resume.
- LLM fact extractor with strict extractive rules (no invented experience, no RAG from LLM work).
- Deduplication engine: exact match, near-duplicate detection (name token overlap), merge candidate.
- Fact import batches with undo support (POST /v1/users/{user_id}/facts/batches/{id}/undo).
- Experience interval extraction from resume dates for deterministic duration calculation.
- Facts DB extended with source_type, source_id, source_text, extraction_method, confidence,
  experience_started_at, experience_ended_at, status, batch_id columns.
- DB migration 0030 for fact ingestion columns and fact_import_batches table.

### User isolation

- Added ownership guard module (app/security/ownership.py) with reusable validation helpers.
- Added get_current_user FastAPI dependency (app/security/dependencies.py).
- RAG search now passes user_id for server-side document scoping.
- 12 cross-user isolation tests covering CV files, facts, applications, matching, and evidence.

### UI

- Application card buttons redesigned: 3 equal-width main buttons (Почему подходит, Материалы и
  решение, Открыть вакансию) plus ⋯ overflow menu (Рассчитать подробно, Отклонить, Компания в
  чёрный список) using the same card-actions grid as search cards.
- Matching details panel: separate sections for confirmed, partial, insufficient evidence,
  evaluation errors, unresolved blockers, and true missing.
- Fact import/extract buttons in Facts section with source_type filter and batch history.
- Cache-Control: no-cache headers for dashboard endpoint.

### Prompt refactoring

- Extracted materials and resume prompts from Python to external markdown files under prompts/.
- Added claim decomposition (v2) and entailment evaluation (v2) prompts to registry.json.
- Decomposition prompt now includes hard_blocker classification guidance.
- Entailment prompt includes concrete examples of what does/doesn't constitute evidence.

### Tests

- 13 new regression tests for matching v2.3 (NLP≠production Python, LLM≠RAG, FastAPI=production,
  duration union, insufficient evidence, evaluation error, hard blocker, unresolved blocker).
- 21 new tests for fact ingestion (file parsing, dedup, resume extraction schemas).
- 12 new tests for user isolation (cross-user access prevention).
- Full suite: 922 passing.


## 1.2.0 (in progress) — 2026-08-01

- Reorganized contributor and agent guidance around a concise `AGENTS.md` plus task-specific
  product, database, browser automation, testing, matching, and workflow documentation.
- Removed completed or contradictory planning documents after transferring their current facts to
  maintained references; application behavior and database schema are unchanged by this audit.
- Made `scripts/setup.ps1` create the required shared Docker network and report the version from
  `VERSION` instead of a stale literal.
- Verified Markdown links and paths, PowerShell syntax, Python compilation, TOML/JSON parsing,
  Docker Compose configuration, and `git diff --check`. The full pytest suite was not run because a
  project virtual environment was unavailable.
- Added user-scoped site fields, semantic mappings, encrypted site/field overrides, and Alembic
  migration `0023` without exposing stored override plaintext in API responses.
- Added deterministic effective-value resolution with explicit precedence, sensitive-value
  blocking, review propagation, and bounded value transformations.
- Extended form discovery with ordered fallback locators and added tenant-safe APIs for field
  discovery, mapping, overrides, and effective-value inspection.
- Added a dashboard mapping table with common-value selection, site/field override controls, and
  effective value/source feedback.
- Verified the Phase 3 boundary with full Ruff, targeted mypy, JavaScript syntax validation,
  Alembic head `0023`, and 644 passing tests; no real application was submitted.
- Started Phase 4 with closed workflow-step and lifecycle enums plus exact privileged/browser
  action sets; arbitrary action types remain rejected.
- Added the versioned `WorkflowDefinitionRow` persistence contract with site ownership, closed
  lifecycle status, URL-pattern storage, uniqueness constraints, and SQLite-verified metadata.
- Added ordered, typed `WorkflowStepRow` persistence with closed action types, bounded timeouts,
  selector candidates, declarative conditions/parameters, and explicit enabled state.
- Added linear Alembic revision `0024` for workflow definitions and steps with dependency-safe
  upgrade/downgrade ordering and explicit server defaults.
- Added user-scoped arbitrary site definitions with strict HTTPS URL validation, exact host
  allowlists, bounded authorization rules, soft archival, and Alembic migration `0022`.
- Added create, list, update, and archive site-definition APIs with tenant isolation and reserved
  compatibility keys for HeadHunter and LinkedIn.
- Generalized browser authorization and session status reporting for custom sites while preserving
  the existing known-site adapters and their login behavior.
- Replaced fixed session cards with safe dynamic dashboard rendering and an arbitrary-site setup
  form; browser credentials, CAPTCHA answers, and secrets remain outside application storage.
- Verified the Phase 2 boundary with full Ruff, targeted mypy, JavaScript syntax validation,
  Alembic head `0022`, and 617 passing tests; no real application was submitted.
- Added canonical, user-scoped autofill values with validated fixed and custom keys.
- Added encrypted storage helpers so persisted autofill plaintext is never stored directly.
- Added create, read, update, and delete service operations with fail-closed validation and
  user isolation.
- Added a user-configurable OpenAI-compatible provider with custom endpoint, encrypted API key,
  model discovery, and native model selection in the dashboard.
- Added a user-scoped read/list API for canonical autofill values and optional endpoint storage
  through migration `0021`.
- Added create, update, and delete autofill API operations plus dashboard sections for personal
  data and application defaults.
- Added fail-closed sensitive-value policy: sensitive values require review, cannot be sent to an
  LLM, and require explicit encrypted-storage consent in the dashboard.
- Renamed the dashboard navigation section to “Profile and access” while preserving its existing
  route and panel identifiers.
- Added the `autofill_values` database migration and an ADR for canonical autofill data and
  declarative automation workflows.
- Verified the Phase 1 boundary with full Ruff, Alembic head `0021`, and 521 passing tests;
  application submission behavior remains unchanged.

## 1.1.300 — 2026-08-01

- Detailed matching now shares uploaded resume artifacts with the dispatcher and uses the
  internal matching-model service address reliably in Docker Compose.
- Gemini structured outputs are constrained by a compatible JSON Schema subset, preventing
  extraction tasks from returning invalid top-level arrays or mismatched fields.
- Detailed-match polling now treats degraded fallback results as terminal instead of appearing
  to remain pending indefinitely.
- LinkedIn vacancy extraction waits for dynamically rendered semantic content, selects the most
  complete description candidate, and removes Premium promotional blocks before persistence.
- The optional local BGE embedding and reranking service now supports the configured CUDA runtime
  and GPU device exposure through Docker Compose.
- Verified the complete detailed-matching path against running Gemini, OpenSearch, PostgreSQL,
  Redis, and the local embedding service; real application submission behavior remains unchanged.

## 1.1.100 — 2026-07-29

- Search results now appear as soon as each selected source completes instead of
  waiting for every source.
- Partial results remain sorted by match score and are saved while slower sources
  continue searching.
- Search progress shows how many selected sources have completed.

## 1.0.0 — 2026-07-28

- Personal dashboard with Russian and English interfaces.
- Multiple resume upload, analysis, selection, skill review, and deletion.
- Browser-based hh.ru and LinkedIn search with dashboard-managed sessions.
- Greenhouse public-board search.
- Saved-vacancy catalogue with filters, pagination, match ordering, summaries, salary and work tags.
- Editable, vacancy-language-aware cover letters.
- Application status synchronization, rejection, manual-submission confirmation, and company blacklist.
- Review queue with vacancy-scoped materials and decisions.
- Optional explainable detailed matching service.
- One-command Windows installation script and Docker Compose runtime.
