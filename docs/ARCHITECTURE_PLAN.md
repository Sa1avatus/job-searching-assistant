# JSA architecture plan

Source: the twelve `jsa-*` missions in `missions2.csv`. The plan is derived from the mission text,
not from the code; each stage begins with a repository audit that may show some steps are already
done. Progress is recorded in the status column below.

## Common principles

- Order of work: read `AGENTS.md`, audit the repository, find root causes, make the smallest
  change, then build, test, and validate in the browser. Unit tests alone are not enough.
- Ownership is enforced on every entity (user, resume, application, email, session, analytics).
  User, Resume, and Application are distinct entities. Deduplication is deterministic.
- Human labels come from humans only. LLM output and application status are never ground truth.
  Provenance and timestamps are kept.
- New behaviour ships behind a feature flag with a fallback. Production defaults do not change
  until a benchmark proves the benefit.
- Never read `.env` or secrets. No commit or push without an explicit request. Preserve other
  people's uncommitted changes. Errors are not masked by suppressions or blind retries.
- Irreversible actions (submit, critical profile/search changes) need human approval; submit is
  idempotent. Prefer persisted state machines to frontend assumptions.

## Layers

```
L0 Stability:   JS modules, auth, duplicate requests
L1 Data:        canonical Vacancy · Profile/Resume/Facts · Browser Session
L2 Feedback:    feedback -> review queue -> human annotation dataset
L3 Ranking:     LTR -> A/B and CPU optimisation
L4 Process:     Application Orchestrator · Email Intelligence
L5 Analytics:   Application CRM -> closed-loop strategy
```

Order: 0 -> (1A ∥ 1B ∥ 1C) -> 2A -> (2B ∥ 4A ∥ 4B) -> 3A -> 3B -> 5A -> 5B.

## Stage 0 — Stabilisation (`jsa-stabilization-v1`)

1. Audit the repository and find root causes.
2. Fix JS syntax/runtime errors, ES-module import/export and module scope, dynamic-import and
   static JS 404s.
3. Fix DOM dependency propagation and `getUserId(...).value` TypeError.
4. Remove mass 401s: one correct auth/session context.
5. Fix the browser-session 409 lifecycle/auth race (minimal; formalised in 1C).
6. Remove duplicate initialisation, event handlers, and API requests.
7. Syntax validation, build, tests, static HTTP checks, browser E2E:
   dashboard -> profile -> resume -> search -> matching -> application -> browser session.
8. Document root causes and the checks actually run.

Gate: the critical path runs with no uncaught errors, no 404s, no unexplained 401s.

## Stage 1 — Data foundation (parallel tracks)

### 1A. Vacancy and search core (`jsa-job-search-core-v1`)
1. Find existing models and ingestion paths.
2. Canonical vacancy, persisted source IDs.
3. Deterministic, owner-safe deduplication.
4. Unified statuses; filters applied by the backend across the whole database (no duplicated
   frontend logic).
5. Deterministic ordering and correct pagination.
6. Company blacklist affects ingestion and new search results; removing it re-allows the vacancies.

### 1B. Profile, resume, facts (`jsa-profile-intelligence-v1`)
1. Separate User and Resume; several resumes and an explicit selected resume.
2. Verified facts (owner, category, value, verified state), used only after confirmation.
3. Preferences and constraints (salary, location, remote, employment) available to matching.
4. user -> resume -> vacancy link; feedback does not require an Application.
5. Ownership isolation tests; dashboard view/edit.

### 1C. Browser session platform (`jsa-browser-session-platform-v1`)
1. State machine: DISCONNECTED, LOGIN_REQUIRED, AUTHENTICATING, AUTHENTICATED, READY, EXPIRED,
   REAUTH_REQUIRED.
2. Store owner, site, metadata, last verification; READY is confirmed by backend/browser
   verification.
3. Sync login completion with browser navigation; the confirm endpoint has no DOM dependency.
4. 401/409 follow the state machine, no endless retries; manual login flow preserved.
5. Frontend shows real backend state; E2E for owner isolation.

Gate: repeat search creates no duplicates, selected resume is explicit, ownership is tested.

## Stage 2 — Feedback integrity and annotation

### 2A. Feedback integrity (`jsa-feedback-integrity-v1`) — needs 1B
1. Pointwise key user/resume/vacancy; protection from direct and reverse pairwise duplicates.
2. Pairwise with vacancy-specific reasons; `both_equal`/`neither` never become fake pairs.
3. Consistent score normalisation.
4. Review priority is an explainable heuristic (not a probability) with deterministic tie-break
   and per-bucket diversity limit.
5. Transaction-safe writes; export validation (labels, duplicates, timestamps, ownership).
6. At least 20 regression tests.

### 2B. Human annotation (`jsa-human-annotation-v1`)
1. Annotation workflow in JSA on top of the review queue.
2. 200-300+ meaningful labels: pointwise and pairwise, hard negatives, model-disagreement cases,
   reasons and confidence.
3. Provenance: annotator, source, timestamp.
4. Train/validation/test split without leakage; a frozen evaluation split.
5. Reproducible export and coverage/class/diversity statistics.

Gate: frozen eval split exists and 200-300 labels are collected. Annotation is human-paced, so
Stage 4 runs alongside it.

## Stage 3 — Ranking (after 2B)

### 3A. Learning to rank (`jsa-ltr-production-v1`)
1. Versioned feature schema (semantic score, requirements coverage, experience, seniority,
   location/remote, employment, salary, language, company preference, history, cross-encoder).
2. LogisticRegression baseline, then LightGBM/LambdaMART.
3. NDCG@10, Recall@10, MRR, Precision@10 on the frozen set vs the current pipeline.
4. Top-N inference path behind a feature flag; production default unchanged.

### 3B. A/B and CPU optimisation (`jsa-matching-ab-optimization-v1`)
1. Baseline (LLM-heavy) vs challenger (embedding -> cross-encoder -> structured/LTR) and hybrid
   fallback, same data and metrics.
2. Latency, throughput, CPU, memory; replay or A/B.
3. Data-driven share of LLM fallback.
4. ONNX/INT8 benchmark for the cross-encoder; no silent quality loss.
5. Reversible rollout via feature flags; the LLM path stays.

## Stage 4 — Application process

### 4A. Application orchestrator (`jsa-application-orchestrator-v1`) — needs 1B, 1C
1. One state machine: shortlist -> review -> generate -> validate -> human approval -> submit ->
   verify -> track.
2. Materials grounded in the selected resume and verified facts; audit-friendly regeneration;
   answers editable before submit.
3. Async apply task with persisted state and recovery after refresh/restart.
4. Idempotent submit after explicit confirmation; result verified, errors never hidden.
5. Source adapters share one lifecycle; manual status supported.
6. Browser E2E for the full review-to-submit flow.

### 4B. Email intelligence (`jsa-email-intelligence-v1`)
1. Ingestion with deterministic message identity and dedup; owner-safe, minimal retention.
2. Explainable email -> application/vacancy/company linking.
3. Classes: informational, interview, screening/action required, rejection, offer; the LLM
   interprets, status updates pass controlled validation.
4. Uncertain emails go to a review queue; timeline without duplicate events.
5. Credentials only through the encrypted mechanism; tests and browser validation.

## Stage 5 — Analytics and closed loop

### 5A. Application CRM (`jsa-application-crm-v1`)
1. One timeline vacancy -> application -> response -> interview -> outcome; immutable or
   audit-versioned events.
2. Recruiter/source metadata; owner-scoped aggregates by source, company, role, resume, strategy.
3. Rates computed from real events; model score is not mixed with outcome.
4. Candidate insights in the dashboard.

### 5B. Closed-loop strategy (`jsa-autonomous-job-strategy-v1`)
1. Explainable recommendations (titles, filters, constraints, resume, matching) with evidence.
2. Caveats for small/biased samples; confidence is not causal effect.
3. Accept/reject by the user; decision history stored; critical changes need approval.
4. Loop: search -> match -> apply -> outcome -> learning -> strategy.

## Dependencies and risks

| Item | Depends on | Note |
| --- | --- | --- |
| 2A Feedback | 1B Profile | key user/resume/vacancy needs a Resume entity |
| 3A LTR | 2B (200-300 labels) | do not start without human labels |
| 3B A/B | 3A + frozen split | challenger includes LTR |
| 4A Orchestrator | 1B, 1C | needs selected resume, facts, reliable session |
| 5A CRM | 4A + 4B | single event store, not two timelines |
| 5B Strategy | 5A + enough outcomes | small samples give hypotheses only |

- Stage 0 and 1C both touch the 409/auth race: 0 = minimal fix, 1C = formalisation.
- Human labels and application outcomes have different semantics; do not mix them.
- The 200-300 label target is a goal, not a proven sufficiency threshold for LTR.

## Status

| Stage | Status |
| --- | --- |
| 0 Stabilisation | replaced: this branch is a rollback to the last stable version, so the stage is the safe extraction of inline CSS/JS from `dashboard.html` and `review.html` into `app/static/assets/{css,js}` served at `/assets` (classic scripts, no modules). Done; the extracted JS is AST-identical to the original. Also fixed: 401 message, `userId` guard in session confirm/cancel, poll errors logged. Verified with the unit/API suite and a browser load; full login/search/apply E2E needs Postgres and is not run |
| 1A / 1B / 1C | done (commits 0957b0a, e0cfd3c, 5fc1dec): canonical vacancy identity + blacklist on new applications (migration 0040), user preferences (0041), browser session state machine (0042). Applied to the persistent DB, which is at 0042 |
| 2A | done (migration 0043, `/v1/annotation/*` mounted, 25+ regression tests) |
| 2B | tooling done (migration 0044: annotation workflow UI, pair queue, leakage-safe folds, freezable eval split, coverage report). **The 200-300 human labels themselves are not collected: that is human work and gates 3A/3B** |
| 3A | tooling done: metrics, logistic baseline, optional LambdaMART, benchmark vs current pipeline on frozen folds, versioned feature schema, guarded training, shadow-only inference behind `APP_LTR_ENABLED`. **No model is trained and no benchmark exists: there are no human labels yet** |
| 3B | tooling done: replay harness, margin-curve routing, `hybrid_rank` with fallbacks behind `APP_MATCHING_HYBRID_ROUTING`, `scripts/matching_ab.py`. **No benchmark/A-B result and no ONNX/INT8 measurement exist** (no labels, no cross-encoder artifact, no onnxruntime) |
| 4A | lifecycle + submission ledger done (migration 0045, crash-safe idempotent real submission, audited transitions). Not done: a generate->validate->approve state machine for materials, async-task recovery UI, browser E2E of review-to-submit (real submit is never run in tests) |
| 4B | done (migration 0046): classes, lifecycle-validated email status updates, ledger for implied submissions, explainable linking, body retention. Deduplication key unchanged (SHA-256 of subject+body; no IMAP Message-ID yet) |
| 5A | done: journey, funnel, insights, immutable timeline, dashboard panel (no migration). Real data: 134 submitted, 3 % response |
| 5B | not started |
