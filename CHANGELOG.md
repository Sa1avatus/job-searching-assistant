# Changelog

## 1.2.0 (in progress) — 2026-08-01

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
