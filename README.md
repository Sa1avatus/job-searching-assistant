# Job Searching Assistant

Recruitment assistant MVP with a personal dashboard, resume analysis, browser-based vacancy search,
matching, individual cover-letter drafts, review, and explicitly enabled browser submission.
HeadHunter and LinkedIn search do not use job-seeker APIs.

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

The personal dashboard is available at `http://127.0.0.1:8000/dashboard`; the detailed review
queue is available at `http://127.0.0.1:8000/review`.

### Enable browser search and submission

Create a Fernet key and place it in `.env` as `APP_BROWSER_STATE_ENCRYPTION_KEY`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `APP_ENABLE_LINKEDIN_APPLY=true` to enable LinkedIn browser search and Easy Apply. Set
`APP_ENABLE_HEADHUNTER_APPLY=true` only if the final hh.ru submission button should be enabled.
After creating a user in the dashboard, capture signed-in sessions in visible browser windows:

```powershell
python scripts/browser_login_capture.py headhunter --user-id <user-id>
python scripts/browser_login_capture.py linkedin --user-id <user-id>
```

The script never asks for a password or verification code. You enter them directly on the site.
CAPTCHA and verification checkpoints are never bypassed automatically.

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
- `POST /v1/vacancies/import-headhunter` for browser-based hh.ru extraction
- `POST /v1/vacancies/import-linkedin-reference` for policy-safe manual LinkedIn references
- `GET /v1/review-queue` and `POST /v1/applications/{application_id}/decision`

The review interface displays answer provenance, missing facts, legal declarations, and active
human-action instructions. Draft edits are stored separately from verified profile facts and never
enable automatic submission.

See `docs/commands.md`, `docs/architecture.md`, `docs/security.md`,
`docs/compliance-matrix.md`, and `docs/known-limitations.md` for verified commands and current
boundaries.

## Current boundaries

Real submission is disabled by default and requires both a feature flag and an explicit confirmation
from the dashboard. LinkedIn automation can trigger platform restrictions; use it only with an
account whose risk you accept. Selectors are fixture-tested but may need maintenance when either site
changes its markup. CAPTCHA, SMS, 2FA, legal declarations, and unknown required questions always stop
for human action.
