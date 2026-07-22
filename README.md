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
docker compose --profile browser up --build -d
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

With the supplied Docker configuration, the personal dashboard is available at
`http://127.0.0.1:8000/dashboard`; the detailed review queue is available at
`http://127.0.0.1:8000/review`. Set `APP_HTTP_PORT` in `.env` to choose another host port.
The dashboard uses separate menu sections for saved vacancies, search, the company blacklist,
resume, browser sessions, the LLM, and local access. **Saved vacancies** lists the selected user's
active applications with server-side text/source/status/location/score filters and 20-item
pagination, ordered by match score. Rejected/skipped vacancies and blacklisted companies are hidden
from the default view and from later discovery runs.

### Enable browser search and submission

Create a Fernet key and place it in `.env` as `APP_BROWSER_STATE_ENCRYPTION_KEY`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `APP_ENABLE_LINKEDIN_APPLY=true` to enable LinkedIn browser search and Easy Apply. Set
`APP_ENABLE_HEADHUNTER_APPLY=true` only if the final hh.ru submission button should be enabled.
After creating a user, use the **Site sessions** cards in `/dashboard`: click the sign-in button,
complete sign-in in the visible Chromium window, then click **I signed in — save**. The dashboard
shows whether an encrypted session exists for both hh.ru and LinkedIn. The application never asks
for a password or verification code; you enter them directly on the site. CAPTCHA and verification
checkpoints are never bypassed automatically.

### Choose an LLM in the dashboard

After creating a user, select Google Gemini or Anthropic Claude in `/dashboard`, paste the
provider API key, and click **Get models**. The list is loaded from the provider's official model
API. Select a model and save it. The API key is encrypted with
`APP_BROWSER_STATE_ENCRYPTION_KEY` before PostgreSQL persistence and is never returned to the
browser. Environment-based LLM settings remain a fallback for users without a saved preference.

Resume upload accepts PDF, DOCX, legacy DOC (best-effort text recovery), TXT, RTF, ODT, HTML/HTM,
and Markdown. Scanned/image-only documents still require OCR before upload.

- `GET /health` and `GET /ready`
- `GET /metrics`
- `GET /v1/connectors` for explicit capabilities and safety limitations
- `POST /v1/connectors/browser-handoff` for manual hh.ru/LinkedIn browser continuation
- `POST /v1/assessments`
- `POST /v1/users` and `POST /v1/users/{user_id}/facts`
- `GET /v1/users/{user_id}/vacancies` for filtered, paginated saved vacancies
- `GET/POST/DELETE /v1/users/{user_id}/company-blacklist` for company exclusions
- `POST /v1/users/{user_id}/cv-files` for validated resume uploads
- `POST /v1/llm/models` and `GET/PUT /v1/users/{user_id}/llm-preference` for user LLM setup
- `GET /v1/users/{user_id}/browser-sessions` plus `POST` to its per-site `start`, `confirm`, and
  `cancel` routes for dashboard-driven hh.ru/LinkedIn sign-in
- `DELETE /v1/users/{user_id}` for profile/application/task deletion
- `POST /v1/vacancies` and `POST /v1/applications/prepare`
- `GET /v1/applications/{application_id}/task` for durable dispatch evidence
- `PATCH /v1/applications/{application_id}/materials` for user-reviewed cover letters and answers
- `POST /v1/applications/{application_id}/retry` for an audited retry of recoverable tasks
- `POST /v1/applications/{application_id}/resume` for an active human-action checkpoint
- `POST /v1/applications/{application_id}/reject-vacancy` to hide a saved vacancy permanently
- `POST /v1/applications/{application_id}/prepare-browser-review` for validated, non-submitting
  Greenhouse form preparation in the isolated Chromium worker
- `GET /v1/evidence/{artifact_id}` for root-confined, no-store screenshot evidence
- `POST /v1/vacancies/import-greenhouse` for strict public read-only extraction
- `POST /v1/vacancies/import-headhunter` for browser-based hh.ru extraction
- `POST /v1/vacancies/import-linkedin-reference` for policy-safe manual LinkedIn references
- `POST /v1/users/{user_id}/discover-greenhouse-vacancies` for known/supplied company boards
- `GET /v1/review-queue` and `POST /v1/applications/{application_id}/decision`

Browser discovery expands a multi-word search into a bounded set of queries: the complete phrase,
comma/semicolon-separated phrases, and individual words. Results are deduplicated by source URL
before extraction, which improves recall without allowing an unbounded number of site requests.
LinkedIn vacancies without Easy Apply remain visible for manual continuation. Cover-letter drafts
follow the detected vacancy language, including a validated Russian-language path.

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
