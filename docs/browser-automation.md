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
