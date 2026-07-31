# Commands

## Local development

```powershell
python -m pip install -e ".[dev]"
python -m playwright install chromium
Copy-Item .env.example .env
python -m alembic upgrade head
python -m uvicorn app.api.main:app --reload
```

## CLI

```powershell
python -m app.cli init-db
python -m app.cli import-profile examples/profile.json --display-name "Candidate"
python -m app.cli add-vacancy https://example.test/jobs/42 --title "Engineer" --company "Example" --required-skill Python
python -m app.cli list-tasks
python -m app.cli review-applications
python -m app.cli run-worker
```

The supported migration target is PostgreSQL. With Compose running, verify that the complete
Alembic chain reached its head revision:

```powershell
docker compose exec -T api alembic current
```

Repository tests use SQLite schemas created from SQLAlchemy metadata, but the Alembic chain is not
portable to an empty SQLite database because earlier migrations add foreign-key constraints.

## Verification

```powershell
python -m ruff format --check app adapters tests migrations scripts
python -m ruff check app adapters tests migrations scripts
python -m mypy app adapters scripts
python -m pytest -q
```

Browser tests use a controlled local HTML fixture and never send an external application.

## Docker

```powershell
docker compose up --build -d
Invoke-RestMethod http://localhost:8000/health
docker compose down
```

Build and run the isolated Chromium worker. Generate the Fernet key once, put it in the local
ignored `.env`, and keep a secure backup: losing or rotating it invalidates saved sessions.

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Copy the output into APP_BROWSER_STATE_ENCRYPTION_KEY in .env; never commit .env.
docker compose --profile browser up --build -d browser-worker
docker compose --profile browser ps
```

Verify a complete Chromium close/reopen with encrypted cookie and localStorage restoration. This
uses only a loopback fixture and an ephemeral key:

```powershell
docker compose --profile browser run --rm --no-deps browser-worker python scripts/browser_session_smoke.py
```

Verify durable SQL queue isolation and the review boundary in the running browser worker. The
smoke uses only the packaged fixture, rejects arbitrary target URLs, and never submits:

```powershell
docker compose exec -T api python scripts/browser_queue_smoke.py
docker compose exec -T api python scripts/greenhouse_queue_boundary_smoke.py
```

After reviewing and saving all required Greenhouse answers in the web queue, schedule background
form preparation explicitly. The fixed confirmation value documents that this action cannot submit:

```powershell
$body = '{"confirmation":"prepare_without_submission"}'
Invoke-RestMethod -Method Post -ContentType application/json -Body $body `
  http://127.0.0.1:8000/v1/applications/{application_id}/prepare-browser-review
```

Run the real Redis coordination smoke test while Compose is up:

```powershell
python scripts/redis_smoke.py
```

Run a public read-only Greenhouse adapter smoke test (no authentication and no submission):

```powershell
python scripts/greenhouse_read_smoke.py https://job-boards.greenhouse.io/{board}/jobs/{job_id}
```

Run a public read-only HeadHunter smoke test for an explicit vacancy. If anonymous access is
CAPTCHA-limited, configure `APP_HH_ACCESS_TOKEN` through `.env` first:

```powershell
python scripts/headhunter_read_smoke.py https://hh.ru/vacancy/{vacancy_id}
```

Verify LinkedIn reference import locally without making any request to LinkedIn:

```powershell
python scripts/linkedin_reference_smoke.py
```

Upload a CV with the API after creating a user:

```powershell
curl.exe -F "file=@C:\path\resume.pdf;type=application/pdf" http://localhost:8000/v1/users/{user_id}/cv-files
```

PDF, DOCX, DOC, TXT, RTF, ODT, HTML/HTM, and Markdown files are accepted. The default maximum size
is 5 MiB; stored names are generated UUIDs and the original filename is retained only as metadata.
Upload may be repeated for additional resume versions. List them with
`GET /v1/users/{user_id}/cv-files`, save reviewed analysis with
`PUT /v1/users/{user_id}/cv-files/{cv_file_id}/profile`, and choose the search default with
`PUT /v1/users/{user_id}/active-cv-file`. The dashboard exposes all three operations without
requiring command-line use.

With Compose running, verify the complete upload-to-review path and cleanup:

```powershell
python scripts/cv_api_smoke.py
```

Verify the live durable dispatcher while Compose is running:

```powershell
python scripts/dispatcher_smoke.py
```

Verify editable materials and answer provenance against the Compose API and PostgreSQL:

```powershell
docker compose exec -T api python scripts/materials_api_smoke.py
```

Prove concurrent claim exclusion against the Compose PostgreSQL instance:

```powershell
python scripts/postgres_claim_smoke.py --database-url postgresql+psycopg://recruitment:recruitment@localhost:5432/recruitment
```
