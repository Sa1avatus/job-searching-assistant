# Execution plan: initial operational recruitment workflow

## Goal

Deliver the constitution's initial stop condition: a locally runnable, restart-safe, review-before-
submit application workflow using PostgreSQL and headless Playwright against a controlled fixture.

## Current state

The operational vertical slice runs under Docker Compose with FastAPI, PostgreSQL/Alembic, Redis,
durable dispatch and retention workers. Controlled Playwright form filling, public read-only adapter
ingestion, persistent review checkpoints, API/CLI/web review, and audited human decisions/retries are
implemented and covered by the repository quality gates. External submission remains disabled.

## Scope

- Typed configuration and structured logging.
- PostgreSQL SQLAlchemy schema and Alembic migration for the operational vertical slice.
- Vacancy/profile/application/task repositories and a resumable workflow service.
- FastAPI health, vacancy ingestion, workflow, and review endpoints.
- Playwright browser wrapper and controlled local fixture application.
- Docker Compose, CI, commands, architecture, security, and limitations documentation.

## Non-goals

- Real job-board submission, CAPTCHA bypass, email monitoring, LLM providers, semantic memory,
  Kubernetes, and automatic production selector repair.
- Claiming support for an ATS before its real integration suite passes.

## Risks and compatibility

- Playwright browser binaries and Python packages require network installation.
- Docker may be unavailable locally; SQLite may be used only for deterministic repository tests,
  while PostgreSQL remains the supported runtime database.
- The review boundary must remain deterministic and cannot be overridden by generated text.

## Steps

- [x] Inspect and establish baseline.
- [x] Implement configuration, persistence, migrations, API, and workflow.
- [x] Implement controlled browser fixture and end-to-end flow.
- [x] Add deployment, CI, architecture, security, and command documentation.
- [x] Install dependencies and execute all locally available quality gates.
- [x] Inspect failures, repair them, and record exact verification.

## Verification

`pytest`, Ruff, mypy, Alembic migration, API tests, and a Playwright end-to-end test against a local
fixture. Docker Compose health checks will be run when Docker is available.

## Progress notes

- 2026-07-18: Repository contained only `AGENT.md`; dependency-free operational policy core added.
- 2026-07-18: Seven unit tests and sample CLI assessment passed under Python 3.12.
- 2026-07-18: Production dependencies and Chromium installed; controlled browser workflow passed.
- 2026-07-18: Ruff, strict mypy, 12 tests, and Alembic SQLite migration smoke test passed.
- 2026-07-18: Docker Compose configuration validated, but the Docker Desktop daemon was not running,
  so the PostgreSQL container startup could not be executed.
- 2026-07-18: Docker Desktop became available; PostgreSQL 16 started healthy, Alembic used
  `PostgresqlImpl`, readiness passed, and a real persistent HTTP review/approval flow completed.
- 2026-07-18: Constitution gap audit recorded in `docs/compliance-matrix.md`; Phase 3–6 remain
  materially incomplete and are not represented as delivered.
- 2026-07-18: Compact API image built successfully, Compose API/PostgreSQL services became healthy,
  and containerized `/ready` returned review mode.
- 2026-07-18: Added production API-key boundary, deterministic form discovery, Greenhouse contract,
  Redis leases/rate limiting, stale-task recovery, prompt registry, and redacted JSON logging.
- 2026-07-18: Added browser failure evidence, retention and user deletion, scoped tool registry,
  model cost/timeout gates, and the constitution's operational CLI command set.
- 2026-07-18: Greenhouse public Job Board API extraction passed a live read-only smoke. Migration
  `0002` persists adapter evidence/form schema and sensitive fields propagate into review warnings.
- 2026-07-18: Added a PostgreSQL/Redis durable dispatcher, live skip-locked concurrency smoke,
  responsive review UI, atomic human-decision completion, visible workflow state, and audited retry.
- 2026-07-18: Added migration `0007` for durable human-action/CAPTCHA checkpoints, one-shot resume,
  review UI instructions, transition evidence, and a live PostgreSQL/API/browser verification.
- 2026-07-18: Added migration `0008` for UUID-addressed, root-confined PNG evidence with structural
  validation, scoped no-store delivery, on-demand UI rendering, and deletion synchronization.
- 2026-07-18: Added migration `0009`, authenticated encrypted Playwright state outside PostgreSQL,
  synchronized retention/user deletion, a dedicated Chromium worker image, session audit heartbeat,
  and a controlled close/reopen recovery smoke.
- 2026-07-18: Completed `FORM-001`: all constitution field types, radio grouping, accessible/context
  labels, HTML constraints, deterministic answer validation, and pre-fill enforcement now pass a
  controlled exhaustive browser fixture.
- 2026-07-18: Kept Playwright after security/compatibility review and a repository-local benchmark
  showed Rustwright `0.1.1` approximately twice as slow; evidence and reconsideration gates are in
  ADR 0002.
- 2026-07-18: Completed `SELECTOR-001`: bounded semantic fallback, atomic root-confined versioned
  mappings, reversible history, reuse after restart, and a controlled redesign regression test.
- 2026-07-18: Added migration `0010` with durable queue routing and typed task payloads. The isolated
  Chromium worker claimed a controlled browser task from PostgreSQL, reached `waiting_for_user`, and
  produced verified screenshot evidence with `submission=false`; all six Compose services were up.
- 2026-07-18: The full chain is explicitly PostgreSQL-only. An empty SQLite Alembic smoke was removed
  from current commands because historical migration `0003` requires adding foreign keys; SQLite
  remains limited to repository tests created from SQLAlchemy metadata.
- 2026-07-18: Completed `ATS-001` acceptance behavior: live public Greenhouse extraction was already
  verified, and typed review preparation now rejects unknown/missing answers, fills a controlled ATS
  fixture through semantic locators, captures evidence, and never exposes a submit action.
- 2026-07-18: Completed `GREENHOUSE-BROWSER-001`: reviewed applications can be explicitly routed
  API→PostgreSQL→isolated Chromium worker with a workflow-only payload. Required answers, sensitive
  provenance, exact hosts, redirects, and managed CV paths fail closed; the review UI exposes a
  clearly labelled no-submit action. Compose fixture and arbitrary-URL boundary smokes passed.
- 2026-07-18: Browser review screenshots are now registered as protected evidence together with the
  human checkpoint and final task transition. Application review resolves only through an explicit
  decision, preventing the generic resume action from rerunning a completed form preparation loop.
- 2026-07-22: Added Anthropic/Gemini selection, live provider model discovery, encrypted per-user
  API-key persistence, user-bound model routing, extended resume formats, and migrations `0011` and
  `0012`. Live Gemini discovery returned 41 models and the saved key was absent from API responses.
  Ruff, mypy, 127 tests, Alembic drift check, Docker builds, and port-8000 smoke tests passed.
- 2026-07-22: Added the dashboard vacancy catalog with per-user server-side filtering and
  pagination, menu-based settings/search/resume/session navigation, and bounded phrase/word search
  expansion with URL deduplication for hh.ru and LinkedIn. The live PostgreSQL catalog returned 202
  user vacancies across 11 pages; LinkedIn filtering returned 81 records and browser UI checks
  confirmed pagination, filtering, status labels, and an error-free console. The Windows server
  launcher now pins imports to this project root instead of a previously installed package copy.
- 2026-07-22: Added migration `0013` and per-user company blacklists, permanent vacancy rejection,
  default hiding of rejected/skipped applications, match-score ordering, accessible red-to-green
  score meters, and Russian cover-letter language enforcement. Greenhouse board discovery was
  live-verified with two staged jobs. LinkedIn's July 2026 randomized-class interface was captured,
  the adapter was repaired using canonical job links and document metadata, and one live LinkedIn
  job was then extracted and staged successfully. The live catalog showed 148 visible vacancies in
  descending score order; 141 tests, Ruff, and strict mypy passed before final UI verification.

## Final result

The initial controlled operational increment is implemented and locally verified. It reaches a
durable screenshot-backed review checkpoint without external submission, survives restart, and
runs with PostgreSQL, Redis, API, dispatcher, and retention services under Docker Compose. The
dashboard now also supports encrypted per-user LLM settings and expanded resume ingestion.
