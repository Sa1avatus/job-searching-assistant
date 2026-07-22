# Job Searching Assistant

Policy-safe recruitment automation foundation. The current increment implements verified-profile
matching, guarded answer preparation, automatic-submission policy, auditable workflow state,
PostgreSQL migrations, Redis coordination, a protected API, versioned prompts, and a headless
Playwright review flow with encrypted restartable browser state in an isolated worker. It also performs public read-only Greenhouse vacancy extraction and safely
stores selected PDF/DOCX CV files. It does **not** submit real applications yet.

Workflow tasks can be persisted with `JsonTaskRepository` for the dependency-free local slice. The
Compose runtime uses PostgreSQL skip-locked claims and Redis leases for multi-worker-safe dispatch;
domain code does not depend on either persistence implementation.

## Verified local usage

Python 3.12+ is required. Install the project dependencies before running commands and tests:

```powershell
python -m app.cli assess --profile examples/profile.json --vacancy examples/vacancy.json
python -m pytest -q
```

For the API, PostgreSQL, migrations, and Playwright worker:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
playwright install chromium
Copy-Item .env.example .env
docker compose up --build -d
```

## Safety defaults

- Submission mode defaults to `review`.
- Unverified profile facts never influence matching or answers.
- Missing required facts block automatic submission.
- Sensitive declarations always require human review.
- Automated tests use only `example.test` and never submit externally.
- Browser cookies/localStorage are stored only in root-confined Fernet-encrypted files; the database
  contains lifecycle metadata, not session secrets.

## API

The local human review interface is available at `http://127.0.0.1:8000/review`.

- `GET /health` and `GET /ready`
- `GET /metrics`
- `GET /v1/connectors` for explicit capabilities and safety limitations
- `POST /v1/connectors/browser-handoff` for manual hh.ru/LinkedIn browser continuation
- `POST /v1/assessments`
- `POST /v1/users` and `POST /v1/users/{user_id}/facts`
- `POST /v1/users/{user_id}/cv-files` for validated PDF/DOCX CV uploads
- `DELETE /v1/users/{user_id}` for profile/application/task deletion
- `POST /v1/vacancies` and `POST /v1/applications/prepare`
- `GET /v1/applications/{application_id}/task` for durable dispatch evidence
- `PATCH /v1/applications/{application_id}/materials` for user-reviewed cover letters and answers
- `POST /v1/applications/{application_id}/retry` for an audited retry of recoverable tasks
- `POST /v1/applications/{application_id}/resume` for an active human-action checkpoint
- `POST /v1/applications/{application_id}/prepare-browser-review` for validated, non-submitting
  Greenhouse form preparation in the isolated Chromium worker
- `GET /v1/evidence/{artifact_id}` for root-confined, no-store screenshot evidence
- `POST /v1/vacancies/import-greenhouse` for strict public read-only extraction
- `POST /v1/vacancies/import-headhunter` for official hh.ru public API extraction
- `POST /v1/vacancies/import-linkedin-reference` for policy-safe manual LinkedIn references
- `GET /v1/review-queue` and `POST /v1/applications/{application_id}/decision`

The review interface displays answer provenance, missing facts, legal declarations, and active
human-action instructions. Draft edits are stored separately from verified profile facts and never
enable automatic submission.

See `docs/commands.md`, `docs/architecture.md`, `docs/security.md`,
`docs/compliance-matrix.md`, and `docs/known-limitations.md` for verified commands and current
boundaries.

## Current boundaries

No authenticated ATS submission adapter, external account, or automatic submission is enabled.
Greenhouse supports live public read-only extraction plus durable, non-submitting review-form
preparation; the browser execution contract and Chromium behavior are fixture-verified, while a live
external fill is not claimed. HeadHunter support is public and read-only. LinkedIn is reference/manual-import only
because job seeker scraping and unauthorized automation are prohibited. Semantic memory, production
ATS selector mappings, multi-user ownership authorization, and the broader analytics schema remain
subsequent increments. No external credentials are required for the current controlled workflow.
