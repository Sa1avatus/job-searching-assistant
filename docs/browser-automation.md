# Browser automation and external actions

Read this document before changing Playwright, browser sessions, browser workers, job-board
adapters, selector recovery, form filling, or any action that can affect an external account.

## Component boundaries

- `app/browser/engine.py` owns typed Playwright actions and evidence.
- `app/browser/session_store.py` stores authenticated state as encrypted, root-confined files.
- `app/browser/session_service.py` owns browser-session metadata and lifecycle.
- `app/workers/browser_worker.py` owns the isolated Chromium runtime and browser queue.
- `app/workers/browser_tasks.py` reloads persisted inputs and enforces task-specific policy.
- `adapters/job_boards/` contains source-specific URL, extraction, and application behavior.
- `config/browser/headhunter_apply.json` contains validated HeadHunter selectors and confirmation
  text mounted read-only into API and browser-worker containers.

Generated text cannot call these components directly. A service must validate model output into
domain types, then deterministic policy decides whether a browser task is allowed.

## Session and navigation safety

Saved credentials, passwords, one-time codes, and CAPTCHA answers are never captured. The user signs
in through a visible local browser and explicitly saves only encrypted Playwright storage state.
PostgreSQL keeps lifecycle metadata and a relative path, not cookies or localStorage values.

Every integration must validate HTTPS hosts before navigation and fail closed on an unexpected
redirect. Do not add arbitrary page JavaScript, CAPTCHA solving, stealth, fingerprint evasion,
credential entry, or proxy rotation. Locator recovery is semantic, bounded, versioned, and promoted
only after a real action succeeds.

## Connector behavior

- **HeadHunter:** dashboard search uses the user's saved session. The lower-level adapter can read
  public search/vacancy pages without a session. Submission is a separate opt-in operation.
- **LinkedIn:** search and native Easy Apply require a saved session and the explicit feature flag.
  External-redirect applications are not followed.
- **Greenhouse:** public board discovery is read-only. Browser preparation uses stored, validated
  answers and a managed resume, captures evidence, and stops before submit.
- **Configured sites:** site definitions, fields, mappings, and overrides exist. The declarative
  workflow executor is not complete; see `universal-site-automation.md`.

Detailed selector and connector limitations belong in `adapter-guide.md` and
`known-limitations.md`, not in agent instructions.

## Verification levels

Safe local checks use controlled files under `fixtures/` and `tests/fixtures/`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/browser -q
.\.venv\Scripts\python.exe -m pytest tests/end_to_end/test_controlled_application.py -q
docker compose --profile browser run --rm --no-deps browser-worker python scripts/browser_session_smoke.py
docker compose exec -T api python scripts/browser_queue_smoke.py
docker compose exec -T api python scripts/greenhouse_queue_boundary_smoke.py
```

The Docker smoke commands create local containers and artifacts but do not submit externally.

The following require explicit user approval and are not routine verification:

- opening a saved authenticated session;
- running HeadHunter or LinkedIn discovery against a real account;
- running any live-site smoke script;
- enabling `APP_ENABLE_HEADHUNTER_APPLY` or `APP_ENABLE_LINKEDIN_APPLY`;
- exercising an apply endpoint or final submit control.

Never claim external compatibility from fixture tests alone. Record the date, account scope, and
exact read-only or mutating boundary when an authorized live check is performed.

## Session lifecycle (state machine)

Each user/site pair has an explicit state stored in `browser_session_states` (migration 0042) and
defined in `app/domain/browser_session_state.py`; the dashboard renders it instead of inferring
login status.

`DISCONNECTED -> AUTHENTICATING` (login started) `-> AUTHENTICATED` (capture saved) `-> READY` (the
site confirmed the session; `last_verified_at` is set). A failed verification goes `EXPIRED ->
REAUTH_REQUIRED`; an abandoned or vanished login window goes to `LOGIN_REQUIRED`, except that a user
who already had a working capture keeps it.

- `confirm` is only accepted while `AUTHENTICATING`; otherwise the API answers 409 with
  `{code, message, state, recovery}` and the dashboard re-reads the real state. There are no blind
  retries.
- A worker that cannot be reached leaves the state untouched (unknown is not "not waiting"); a
  window seen as gone is only dropped after a 15 s grace period.
- Sessions captured before this table existed are bootstrapped from `browser_sessions`
  (`available` -> `AUTHENTICATED`, `expired`/`corrupted` -> `REAUTH_REQUIRED`).
- State is per user; another user's session never affects it.

## Application lifecycle and crash-safe submission

**Lifecycle** (`app/domain/application_lifecycle.py`, applied by
`app/services/application_lifecycle.py::change_status`). One transition table governs every status
change: shortlist (`draft`/`saved`) -> review (`awaiting_review`) -> human approval (`approved`) ->
`submitted` -> tracking (`interview`/`offer`/`employer_rejected`), with `rejected`/`skipped`/
`withdrawn` exits and `needs_review` for anything unclassified. Nothing moves backwards once
submitted (that is how a duplicate submission would be prepared), `withdrawn` is final, and
`employer_rejected` can only go to `needs_review`. A person may record what happened outside the app
(pre-submission -> interview/offer/rejection), which also records a manual submission. Automation
cannot take human-only transitions (approval, restoring a rejected/skipped entry). Setting the same
status again is a no-op. Every real change writes a timeline event (`status_change`, with its
source). `PATCH /v1/applications/{id}/status` answers an illegal change with a structured 409
`{code: illegal_transition, message, current, requested, allowed}`.

**Submission ledger** (`application_submissions`, migration 0045). A real submission is recorded
*before* the browser touches the site (`attempting`) and resolved afterwards: `confirmed`,
`unknown` (adapter could not tell, e.g. `ApplyBlocked`) or `failed` (definitely not sent: no session,
CAPTCHA before submitting). A partial unique index allows only one attempting/unknown/confirmed row per
application. Consequences:

- a crashed worker leaves `attempting`; the next run **probes the site** (`has_submitted_application`)
  and marks the application submitted if it shows the earlier attempt, otherwise it waits for a human
  (`verify_submission`) - it never resubmits blindly;
- a confirmed submission is never sent again, even if the status was reset by mistake;
- scheduling a new real submission is refused (409) while an attempt is open or confirmed;
- `GET /v1/applications/{id}/submission` shows the state, every attempt and the allowed next statuses;
  `POST /v1/applications/{id}/submission/resolve {"submitted": true|false}` is the human answer for an
  unresolved attempt (`false` frees exactly one new attempt; nothing is submitted by the call);
- site probes (sync, discovery) that see an application as submitted record a `confirmed` row too;
- the migration backfills a `confirmed` row (`verified_by='legacy'`) for every application already
  submitted/interview/offer.

Real submission is still gated by `APP_ENABLE_*_APPLY`, an explicit confirmation request and a
captured session; this change adds no way to submit without them.
