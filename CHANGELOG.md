# Changelog

All notable changes to Job Searching Assistant are documented in this file. The project follows
semantic versioning for new releases; older historical version numbers are preserved as released.

## [Unreleased]

### Added

- **Record a search scenario instead of a URL template.** For sites where the URL-template
  search recipe cannot express the search (a POST form, an in-page click), the "Записать сценарий
  поиска" button opens the same visible, noVNC-streamed browser session already used to sign in
  to a site. A fixed, reviewed script observes clicks and completed field edits - it never acts on
  the page itself - and the person tags which typed field was the query and which was the
  location. The result is a short `navigate`/`fill`/`click` reach-scenario (ADR 0003's closed
  workflow-step schema, reused as-is) stored alongside the recipe's card/link/title/company
  selectors; at search time it is replayed with the same host allowlist and fail-closed
  cross-host check as everything else in `app/browser/custom_site.py`. No migration was needed:
  the recipe was already a single JSON column.
- **Search on user-defined sites.** A site added on the "Сессии сайтов" tab can now be searched.
  A versioned, declarative search recipe (HTTPS URL template with `{query}`/`{location}` plus plain
  CSS selectors for the result card, link, title and company) is learned from a results page the
  person produced themselves - the URL of the page they ended on and what they searched for - or
  written by hand. A recipe stays a draft until a real test run returns results, then it can be
  activated (older versions stay for rollback) and the site appears as a search source (`custom:<key>`).
  Vacancies are read from JSON-LD `JobPosting` when present, otherwise from the page text. Pages are
  read through Playwright locators only; navigation is limited to the site's approved hosts and no
  page JavaScript runs (ADR 0003). Custom vacancies are review-only: nothing is submitted
  automatically. Migration `0048` adds `site_search_recipes`.

### Fixed

- **hh.ru search saved nothing.** `POST /v1/browser/extract-headhunter` declared its request model
  below the route, so with postponed annotations FastAPI read `request` as a required query
  parameter and every extraction returned `422`. Both extract models now precede their routes and a
  contract test covers every POST route of the browser worker.
- **hh.ru `AttributeError` during discovery.** The browser-worker JSON was wrapped without restoring
  types, so form fields arrived as dicts and `published_at` as a string. `_ExtractedVacancy` now
  rebuilds `FormField`, enums, tuples and datetimes. Failed sources are logged with a traceback and
  the discovery stream reports a readable message for custom-site failures.
- **Redundant RAG ingestion in direct-rerank search.** `discover-vacancies-stream` with
  `direct_rerank` re-ingested the user's profile and selected CV into RAG once per vacancy found,
  instead of once per search - amplifying load on the RAG service and on the external LLM used for
  matching/materials generation when many vacancies were found at once. The profile/CV is now
  ingested at most once per search (memoized per selected CV).

## [2.0.0] - 2026-09-19

Release 2.0 is a rollback-based stabilisation followed by the twelve-stage roadmap in
`docs/ARCHITECTURE_PLAN.md`. Migrations `0038`-`0047` were added (`0038`/`0039` restore the schema
of the annotation/LTR work that the persistent database already carried). Apply them with
`alembic upgrade head` after a database backup.

### Fixed

- **Browser-worker login window (502 "check the Chromium installation").** `Dockerfile.browser`
  installed the newest `playwright` while the base image ships the browser build of its own tag; one
  `PLAYWRIGHT_VERSION` build argument now drives both. The virtual display no longer races on a fixed
  `sleep`: `scripts/start_browser_worker_with_desktop.sh` waits for Xvfb (`xdpyinfo`, package
  `x11-utils`), then starts x11vnc, websockify and the HTTP server, and fails/restarts without a
  display.
- **Annotation candidate pool.** Only applications with an explicit selected resume (15 of 917 in the
  real database) and placeholder scores from pending/failed matches were considered; the pool is now
  the scored results of the user's applications for the resume (applications without a selection count
  for the active resume).
- **Stratified sampler** quotas counted only new members, so overlapping strata starved each other.
- **Dashboard**: clear message for 401 responses, the browser-session confirm/cancel actions guard an
  empty user id, background poll errors are logged instead of swallowed, `/favicon.ico` no longer
  404s, and the annotation panel lists splits instead of probing a missing one.

### Changed (2.0)

- **Static assets**: dashboard and review scripts/styles moved out of the HTML into `app/static/assets`
  (`/assets`, `Cache-Control: no-cache`); the extracted JavaScript is AST-identical to the original.
- `numpy` is no longer required by the API image (rank normalisation is pure Python); LightGBM,
  scikit-learn and numpy are the optional `ltr` extra.
- Version strings: `VERSION`, `pyproject.toml`, OpenAPI `info.version` and both READMEs say 2.0.0.

### Added

- **Legacy label import** (`scripts/import_legacy_annotations.py`): moves human labels an older JSA
  version kept in the timeline onto an existing resume (dry run by default, idempotent, reversible,
  `source=legacy_timeline`).

- **Closed-loop job strategy** (migration 0047): evidence-gated recommendations (matured sample,
  non-overlapping intervals) with stated confounders, explicit accept/reject decisions stored as an
  audit trail, one narrow reversible action (switch active resume), and before/after follow-up.
  Says what is missing when the data cannot support advice.

- **Application CRM and analytics**: per-application journey, owner-scoped funnel (source, company,
  resume, role, score band, matching version) with mature-only rates and Wilson intervals, descriptive
  insights, an immutable timeline audit log, and the dashboard panel «Аналитика откликов».

- **Email intelligence hardening** (migration 0046): five email classes in the review API, email-driven
  status changes validated by the application lifecycle (refused moves go to review with a reason),
  implied submissions recorded in the ledger, explainable linking (`match_method`/`match_reason`/
  `review_reason`) and `APP_EMAIL_BODY_RETENTION_DAYS` body retention.

- **Application lifecycle and crash-safe submission** (migration 0045): one transition table for
  every status change with timeline audit and structured 409s; a submission ledger written before
  the browser acts, so a crashed or inconclusive attempt is verified (site probe / human
  `POST .../submission/resolve`) instead of resubmitted, and a confirmed submission is never sent
  twice. Site-verified and manual submissions are recorded too.

- **Matching A/B replay and hybrid routing (off by default)**: replay harness (quality + p50/p95
  latency, throughput, CPU, memory, verdicts), data-driven LLM-share margin curve, `hybrid_rank` with
  baseline/cheap fallbacks behind `APP_MATCHING_HYBRID_ROUTING`, and `scripts/matching_ab.py`.
  No benchmark has been run yet (no human labels); ONNX/INT8 is not measured.

- **Learning-to-rank tooling (shadow only, off by default)**: metrics (NDCG@10, Recall@10, MRR,
  Precision@10), a pure-Python logistic baseline, optional LightGBM LambdaMART, a benchmark against
  the production score on frozen folds, versioned feature schema and model artifacts, guards
  (frozen split + ready dataset, provisional models refused, test fold only on an explicit final
  run) and `scripts/ltr_pipeline.py`. `APP_LTR_ENABLED`/`APP_LTR_MODEL_PATH`/`APP_LTR_ALLOW_PROVISIONAL`
  settings; shadow runs write only `ltr_*` columns.

- **Human annotation workflow and dataset** (migration 0044): dashboard panel «Разметка»
  (pointwise queue, head-to-head pair queue, reasons, confidence; system ranks hidden from the
  reviewer), leakage-safe group folds, freezable evaluation splits, fold-tagged reproducible export
  and a coverage/readiness report against the 200-label target.

- **Annotation/feedback integrity** (migration 0043, `/v1/annotation/*` now mounted): duplicate-proof
  pointwise and order-independent pairwise labels, validated vocabularies with `{code, message}`
  errors, ownership checks, provenance and confidence, transaction-safe submits, deterministic
  review queue with explainable `review_priority` and per-company diversity, and a validated,
  reproducible `GET /v1/annotation/export`. Fixes the stratified sampler (quotas counted only new
  members, so strata starved each other) and a submit path that could not commit.

- **Browser session state machine** (migration 0042): DISCONNECTED, LOGIN_REQUIRED, AUTHENTICATING,
  AUTHENTICATED, READY, EXPIRED, REAUTH_REQUIRED with `last_verified_at`. Status responses gain
  `state`, `last_verified_at`, `last_error`, `recovery_hint`; confirm outside a login answers a
  structured 409 instead of failing in the worker; READY needs a successful site probe.

- **User search preferences** (migration 0041, `GET|PUT /v1/users/{id}/preferences`, dashboard panel):
  minimum salary, preferred locations, work formats and employment types. Mismatches are shown as
  application warnings; they never hide vacancies or change scores.

- **Canonical vacancy identity** (migration 0040): `source_key`, `source_id`, `canonical_url` and a
  `dedup_fingerprint` on vacancies; re-importing the same posting under another URL spelling is now a
  duplicate, the catalog source filter uses `source_key`, and `prepare_application` refuses
  blacklisted companies. Adds `scripts/report_vacancy_duplicates.py` (read-only).

- **Matching queue control** in the dashboard: a new "Очередь матчинга" panel shows the durable
  matching backlog (per-state task counts plus the active task list) and exposes three actions —
  **stop** (pause the worker from claiming new tasks), **clear** (pause the queue, cancel the
  pending/scheduled/retry backlog, and interrupt orphaned running tasks), and **resume** (start
  processing again). Backed by `GET /v1/matching/queue` and
  `POST /v1/matching/queue/pause|resume|clear`, plus a Redis pause flag
  (`recruitment:matching:queue:paused`) that the matching worker checks before claiming each task,
  so a runaway queue can be halted and drained without restarting the worker.

- **RAG + LLM email processing with a review queue.** The «Синхронизировать почту» and email
  import flows now classify each incoming message with the user's LLM and match it to a saved
  vacancy through semantic RAG retrieval over the rag-platform `vacancies` collection. Confident,
  unambiguous matches update the vacancy status automatically (application-received → `approved`
  «Принята», rejection → `employer_rejected`, interview → `interview`, offer → `offer`); ambiguous
  messages get a new `needs_review` status and appear in a dashboard review panel where the user can
  link them to a vacancy or dismiss them. Backed by `GET /v1/users/{id}/email-review` and
  `POST /v1/users/{id}/email-review/{event_id}/resolve`, plus new `category`/`confidence`/
  `subject`/`body`/`needs_review`/`candidates`/`resolved`/`previous_status` columns (migrations
  0036/0037) and the `classify_application_email` prompt.

### Changed

- **Dashboard collapsing**: top-level tabs and panel titles no longer collapse (clicking the active tab keeps it open);
  second-level and deeper sections stay collapsible and each remembers its expanded/collapsed state
  (`localStorage` key `dashboardSubsectionState`, keyed by panel and position, collapsed by default).

- **Raised matching inference budgets for dense vacancies.** Extraction `max_tokens` and the
  extraction/decompose/entailment context windows (`APP_MATCHING_EXTRACTION_MAX_TOKENS`,
  `APP_MATCHING_EXTRACTION_CONTEXT_SIZE`, `APP_MATCHING_DECOMPOSE_CONTEXT_SIZE`,
  `APP_MATCHING_ENTAILMENT_CONTEXT_SIZE`) are now 8192 (was 4096). `num_ctx` is the shared
  input+output window, so a long job description under a 4096 window truncated the extraction
  JSON (`finish_reason='length'` → `NoModelAvailableError` → `UngroundedExtractionError`),
  which failed and retried the run. All three contexts stay equal to avoid Ollama model
  reloads on stage boundaries.

- **Application-received emails now advance to `approved` («Принята»).** The deterministic regex
  classifier gained Russian patterns for «мы получили ваше резюме» and similar acknowledgements,
  and both the regex and LLM paths map `application_received` to `approved` instead of leaving the
  status untouched.

## [1.5.6] — 2026-08-16

### Features

- LLM models are now configured **per purpose**: the Model tab has separate selectors for
  **matching** (extraction, decomposition, entailment) and **materials generation**
  (cover letters, screening answers, resume analysis). Each is stored as its own encrypted
  per-user preference; matching falls back to the materials model until a matching-specific
  model is saved, so existing single-model setups keep working unchanged.

### Fixed

- The application status badge and its change-status popup now sit in the top-right corner
  of each vacancy card (both search results and the "All vacancies" tab), replacing the
  status text in the metadata line and the status editor at the bottom of the card.
- Fixed the saved-vacancy card layout: the topline (company, title, status badge, match
  meter) was being pushed to the middle of the card by a duplicate `card.append(top, …)`
  call, so the badge and match score landed below the attribute tags and key skills instead
  of at the top like the search card. The topline is now the first child again.
- The dashboard's visible sign-in (`browser-sessions/{site}/start`) was always rejected with
  403 on local installs because the Compose file hardcoded `APP_ENVIRONMENT=production`,
  which is reserved for the local-only visible-browser login guard. The value is now
  overridable (default `production`) and the local setup script writes `development`, so the
  sign-in window opens on a local install while a production deployment keeps its API-key
  requirement and browser-login block.
- The visible sign-in now runs in the browser-worker (which has the Playwright browser stack)
  instead of the API container. `browser-sessions/{site}/start|confirm|cancel` delegate to the
  worker's new `/v1/browser/login/*` endpoints; the worker opens the login window on an Xvfb
  display served over noVNC at `:7900` and saves the captured encrypted session itself. This
  removes the browser desktop from the API container and fixes the 502 that surfaced once the
  production guard was lifted.

### Performance

- Matching recalculation now distinguishes a **smart recalculation** (reuses cached LLM
  results; unchanged content returns the existing completed result immediately) from a
  **full recalculation** (discards cached extraction and recomputes from scratch; use
  after changing the LLM model). The dashboard exposes both actions separately.
- Decomposition and entailment LLM results are cached in Redis with content-based,
  model-aware keys. Repeated matching over unchanged or lightly changed content reuses
  prior LLM work instead of re-invoking the model hundreds of times. Cached
  decompositions are rebound to the current requirement row on a cache hit.
- Entailment evaluation stops early after a strong entailed result and evaluates at most
  `APP_MATCHING_ENTAILMENT_MAX_CANDIDATES` candidates per claim (default 2), down from
  the previous full top-k evaluation.
- Simple single-skill requirements (e.g. "Docker", "PostgreSQL") are decomposed
  deterministically without an LLM call.
- Entailment and decomposition requests use per-task output/context budgets
  (`APP_MATCHING_ENTAILMENT_MAX_TOKENS`, `APP_MATCHING_ENTAILMENT_CONTEXT_SIZE`,
  `APP_MATCHING_DECOMPOSE_MAX_TOKENS`, `APP_MATCHING_DECOMPOSE_CONTEXT_SIZE`) instead of
  the previous 16k-output/8k-context defaults, cutting local-model latency. The
  decompose and entailment contexts must match so Ollama does not reload the model
  between stages.
- Matching cache keys and the matching content version include the resolved LLM model, so
  switching the model invalidates prior LLM-derived work automatically.
- Extraction grounding now accepts source fragments whose content words all appear in the
  source text (function-word and punctuation drift tolerated), keeping the
  no-hallucination invariant while allowing weaker local models to pass extraction.
- Extraction prompts now require character-for-character source fragments.
- Entailment evaluation is **batched** (`APP_MATCHING_ENTAILMENT_BATCH_SIZE`, default 5):
  concurrent (claim, evidence) pairs are coalesced by a background flusher and evaluated
  in one LLM call per batch, cutting the actual request count ~5× (a 164-pair cold run
  issues ~35 requests). Per-pair throughput improves ~18% on a local GPU; the pipeline
  is GPU-compute-bound, so wall-time gains are modest while queue/gateway load drops
  sharply. Batches are packed to fit the model context; results are aligned by
  `claim_id` echo with an order fallback; a failed batch degrades per-item to
  `evaluation_error`. Per-pair cache keys are unchanged, so cached results from prior
  runs remain valid; `1` disables batching.
- Entailment evaluation can be routed to a separate model
  (`APP_MATCHING_ENTAILMENT_MODEL`, empty by default): set it to a small local model
  (e.g. `qwen3:1.5b`) and only the entailment classification runs on it, while
  extraction and decomposition stay on the user's configured model. Cache keys
  incorporate the routed model, so switching it never reuses results produced by
  another model.
- Extraction now uses an explicit context and output budget
  (`APP_MATCHING_EXTRACTION_CONTEXT_SIZE`/`APP_MATCHING_EXTRACTION_MAX_TOKENS`,
  both default 4096) aligned with decompose/entailment, so Ollama stops reloading the
  model on every extraction→decompose stage switch. The previous implicit default
  (`num_ctx=8192`, `num_predict=16384`) caused 10-30s model reloads per stage switch and
  queued decompose/entailment calls into the 120s model timeout.
- Version markers are now consistent: `VERSION`, `README`, `README.ru`, and
  `pyproject.toml` all report 1.5.5.
- The OpenAI-compatible provider now adapts to servers that only support `text` and
  `json_object` response formats (e.g. the local-code-worker gateway): when a request
  with a `json_schema` `response_format` is rejected with HTTP 400, the provider
  retries once with `json_object` and remembers the capability for the rest of the
  session. The JSON schema stays embedded in the system prompt, so structured
  extraction, decomposition, and entailment keep working against such endpoints.

## [1.5.1] — 2026-08-14

### Quality

- Restored all green quality gates: Ruff check, Ruff format, mypy, and full pytest (982 passing).
- Added explicit tuple type annotation in `task_repository.py` to resolve mypy assignment error.
- Dashboard matching polling now treats `failed` as a terminal status alongside `scored` and
  `degraded`, matching the test expectation.

### Security

- Added API-level cross-user isolation tests (`test_cross_user_isolation.py`) verifying that
  User A receives 404 when accessing User B's CV files, profile facts, blacklist entries,
  and application statistics. 8 new tests, all passing.
- Combined with existing `test_user_isolation.py` (12 service-level tests), the tenant isolation
  path is now covered at both the service and HTTP layers.

### Testing

- Added RAG E2E smoke test (`test_rag_e2e.py`) exercising owner-scoped ingestion, search,
  isolation, and idempotent upsert across `profiles`, `resumes`, and `vacancies` collections.
  Requires `--rag-e2e` flag and a running RAG service.
- Added RAG fallback client unit tests (`test_rag_fallback.py`) verifying empty results,
  unavailable health, and graceful delete/ingest handling.

### Verified

- RAG collections `profiles`, `resumes`, `vacancies` confirmed present in the RAG platform with
  correct API key permissions (`documents:write`, `documents:read`, `retrieval:search`).
- Resume RAG sync status already displayed in dashboard with retry capability.
- Profile facts RAG sync triggers on create, update, import, and delete.

### Changed

- Added manual batch import of application correspondence from multiple EML files, mbox mailbox
  exports, and ZIP archives. Attached emails are extracted and classified as separate messages;
  imports use the existing deduplication, vacancy matching, and status-update pipeline and retain no
  uploaded source files. Uploads are bounded to 100 selected files, 50 MB, and 500 parsed messages.
- Manual email import now reads HTML-only message bodies, recognizes additional common rejection
  wording, and uses normalized sender display names as conservative company hints. Import summaries
  report unknown and unmatched messages separately from processing errors.
- Matching dispatcher now honors an owner's OpenAI-compatible/local model preference instead of
  incorrectly constructing a Gemini provider with the local model credential.
- All matching LLM stages now honor `APP_MATCHING_MODEL_TIMEOUT_SECONDS`; the prior hard-coded
  60-second request limit caused slower local models to fail even when the configured timeout was
  higher.
- The matching runtime's OpenAI-compatible HTTP client now uses the same configured timeout. Its
  separate 60-second read timeout previously terminated local inference before the matching-stage
  timeout could take effect.
- OpenAI-compatible matching requests now send the actual response JSON Schema instead of generic
  JSON mode. Ollama requests also disable reasoning output, preventing local Qwen models from
  returning valid JSON with schema-incompatible free-form enum and confidence values.
- Ollama structured-output schemas now inline local `$ref` definitions and omit validation-only
  constraints unsupported by its grammar parser. The original complete schema remains in the
  prompt and Pydantic still validates every returned matching payload.
- Email synchronization now recognizes explicit qualification-mismatch rejection wording such as
  `regret to inform you` and `skillset does not match our qualifications`.
- Email-to-application matching now prefers a unique vacancy-title match over a broader company
  match, so several applications to one employer no longer make a clearly titled email ambiguous.
- IMAP synchronization checks the bounded set of recent messages whether read or unread; message
  fingerprints keep repeated synchronization idempotent. Previously, opening a message before sync
  could make the application miss it permanently.
- Previously stored, unmatched `unknown` email events are reclassified and relinked on a later sync
  when improved rules can identify their outcome and application.
- Employer rejections now use the distinct terminal status `employer_rejected` (**Отказ
  работодателя**); `rejected` remains the candidate's own **Отклонена мной** decision. Existing
  email-applied rejections are migrated to the employer status.
- Search-result vacancy cards now expose **Почему подходит** / **Why it matches** from their
  additional-actions menu and expand the same detailed matching explanation used by **All
  vacancies**.
- Resume search keywords now accept up to 2,000 characters, matching the experience-summary limit;
  both fields use equally sized multiline editors with browser-side length limits.
- Vacancy-discovery API requests now accept the same 2,000-character search-keyword value. Long
  comma/newline-separated lists are converted into at most eight site-friendly queries of at most
  200 characters instead of sending one oversized external search query.
- Defined one canonical RAG collection contract: reviewed facts use `profiles`, analysed resumes
  use `resumes`, and vacancies use `vacancies`.
- Resume ingestion no longer writes CV-derived content into the profile-facts collection.
- Confirming an analysed resume now synchronizes it with the owner-scoped `resumes` collection.
- Confirming profile facts now refreshes the owner-scoped `profiles` collection. RAG remains
  fail-open and disabled by default, so authoritative PostgreSQL updates are not lost when the
  optional service is unavailable.
- Creating, editing, or deleting a profile fact now refreshes the reviewed profile document.
- Existing RAG documents are updated through the platform's optimistic-lock `PATCH` contract
  instead of repeatedly submitting conflicting version 1 payloads.
- Deleting a resume removes its owner-scoped RAG document; removing the last verified profile fact
  removes the now-empty profile document.
- Concurrent RAG updates retry optimistic-lock conflicts at most three times.
- Added a bounded, resumable `rag-backfill` CLI command for one owner's existing profile, resumes,
  and vacancies.
- Pytest no longer loads the operator's local `.env`, keeping auth and encryption tests explicit and
  reproducible.
- File and resume fact-import flows now refresh the reviewed profile RAG document after persistence.
- Deleting a user now attempts owner-scoped cleanup of their profile, resume, and vacancy RAG
  documents; individual cleanup failures remain fail-open and do not stop the remaining deletions.
- RAG synchronization and deletion expose aggregate attempt, success, skipped, and failure counters
  through the existing Prometheus `/metrics` endpoint; backfill reports skipped and failed counts
  separately.
- Added an explicit **Send to RAG** action and owner-checked API endpoint for retrying synchronization
  of one confirmed resume.
- Fact extraction now uses the explicitly active resume instead of the newest uploaded file and
  shows its filename before and after extraction.
- Added a bounded owner-scoped `matching-backfill` CLI command for stale and pre-source-v2
  explanations. It reports sanitized failure codes and preserves the previous result while the
  replacement task is pending.
- Evaluator failures now persist only sanitized exception-class codes, never provider response
  bodies. Required requirements affected by a technical error remain reviewable and are no longer
  counted or described as missing skills; the existing explicit retry action uses bounded worker
  attempts.
- Confirmed resumes now schedule idempotent RAG synchronization through the durable dispatcher.
  Synchronization state, attempt count, completion time, and sanitized failure code are visible per
  resume; manual retries receive a fresh bounded attempt budget without duplicating active work.
- Restored the complete static quality baseline: all Python sources are Ruff-formatted, Ruff lint
  and mypy pass without weakening their configuration, and stale matching-admin operations were
  reconciled with the current backfill service.
- Added the missing browser-worker submission-probe contract used by application status sync and
  strict validation for browser-worker vacancy payloads before persistence.

### Tests

- Added collection-routing, update, deletion, and synchronization assertions for profile, resume,
  and vacancy ingestion.

## [1.4.1] — 2026-08-13

### Fixed

- Application-material prompts now receive required, preferred, and extracted vacancy skills.
  Skills shown on vacancy cards are therefore available to the materials workflow even when the
  source supplied them outside the vacancy description.
- Generated materials prioritize only vacancy skills that also occur in verified candidate facts.
  Unconfirmed requirements remain visible as vacancy context but cannot be attributed to the
  candidate.
- JSA now sends the RAG service's required `X-Owner-User-Id` header for retrieval and for profile,
  resume, and vacancy ingestion. User isolation no longer depends on a document metadata filter.
- Synchronized the FastAPI metadata, package, `VERSION`, and README version after the 1.4 release.
- Restored the API logger used by optional vacancy RAG ingestion, preventing its success and
  fallback paths from failing with an undefined name.
- Matching extraction now uses a versioned source containing structured required and preferred
  skills as well as the description. Existing explanations are invalidated when those fields change.
- Every structured key skill is represented by an evaluated requirement and recorded in explicit
  `key_skill_coverage` explanation data.
- Forced recalculation keeps the previous score and explanation while the replacement is pending,
  so a failed refresh does not erase the last usable result.
- **Direct to reranker** is now a server-side search mode: it ingests owner-scoped profile, CV, and
  vacancy context, evaluates an expanded candidate pool, waits for detailed matching, and emits
  the requested number of results in calculated-score order.
- Enabled RAG now augments local verified-evidence ordering before the external reranker; degraded
  or unavailable RAG falls back to the existing local hybrid retrieval.

### Tests

- Added regressions for key-skill propagation, alias-aware candidate confirmation, owner-scoped
  RAG HTTP calls, and owner propagation through all ingestion services.

## [1.4.0] — 2026-08-13

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
- RAG search began passing `user_id` as a server-side metadata filter. Version 1.4.1 replaced this
  provisional mechanism with the RAG API's authoritative owner header.
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


## [1.3.0] — 2026-08-12

### Added

- Skill normalization, component scoring, language matching, hard relevance gates, and expanded
  evaluation fixtures for matching.
- Optional RAG client, context enrichment, and ingestion for vacancies, profiles, and resumes,
  with a local fallback when the service is disabled or unavailable.
- Email event classification, entity extraction, application timeline updates, review queues, and
  safe EML, MBOX, and ZIP import.
- Per-vacancy and bulk reranking, automatic reranking controls, model-health display, and persisted
  dashboard preferences.
- Canonical field taxonomy and form fingerprinting for changed-form detection.

### Changed

- Browser execution was separated into the browser worker, while API-side orchestration retained
  the existing review and safety boundaries.
- Reranking results update persisted scores and vacancy ordering.

### Fixed

- Corrected reranker request sizing, response parsing, health checks, model configuration, and
  end-to-end score updates.
- Corrected RAG vacancy ingestion during discovery and repeated reranking behavior.

## [1.2.0] — 2026-08-09

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
