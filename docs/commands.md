# Development and operations commands

Read this document for setup, local services, Docker lifecycle, migrations, and safe diagnostics.
Testing strategy and selection are in `testing.md`.

## Local Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

Start the API against the configured local services:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload
Invoke-RestMethod http://127.0.0.1:8000/health
```

Useful deterministic CLI examples:

```powershell
.\.venv\Scripts\python.exe -m app.cli assess --profile examples/profile.json --vacancy examples/vacancy.json
.\.venv\Scripts\python.exe -m app.cli init-db
.\.venv\Scripts\python.exe -m app.cli import-profile examples/profile.json --display-name Candidate
.\.venv\Scripts\python.exe -m app.cli add-vacancy https://example.test/jobs/42 --title Engineer --company Example --required-skill Python
.\.venv\Scripts\python.exe -m app.cli list-tasks
.\.venv\Scripts\python.exe -m app.cli review-applications
```

Commands that persist data use the database selected by `APP_DATABASE_URL`.

## Docker Compose

The Windows setup script is the supported first-run path. It creates `.env`, generates missing
encryption material, ensures the shared Docker network exists, builds services, applies migrations,
and waits for health checks:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Manual lifecycle and diagnostics:

```powershell
docker compose --profile browser up --build -d --wait
docker compose --profile browser ps
docker compose logs --tail 200 api dispatcher matching-worker browser-worker
Invoke-RestMethod http://127.0.0.1:8000/ready
docker compose down
```

`docker compose down` preserves named volumes. `docker compose down -v` deletes local application
data and is not a verification command.

Enable the optional GPU matching service only on a compatible machine:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -EnableDetailedMatching
```

## Migrations

Inspecting heads is read-only:

```powershell
.\.venv\Scripts\python.exe -m alembic heads
docker compose exec -T api alembic current
```

Applying migrations mutates the selected database and requires confirmation:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

See `database.md` before changing schema or migration files.

## Controlled local smoke tests

With Compose running, these commands use packaged fixtures or local services and do not submit an
external application:

```powershell
docker compose --profile browser run --rm --no-deps browser-worker python scripts/browser_session_smoke.py
docker compose exec -T api python scripts/browser_queue_smoke.py
docker compose exec -T api python scripts/greenhouse_queue_boundary_smoke.py
.\.venv\Scripts\python.exe scripts\linkedin_reference_smoke.py
.\.venv\Scripts\python.exe scripts\redis_smoke.py
.\.venv\Scripts\python.exe scripts\dispatcher_smoke.py
docker compose exec -T api python scripts/materials_api_smoke.py
```

The PostgreSQL claim smoke writes bounded local test data to the configured development database:

```powershell
.\.venv\Scripts\python.exe scripts\postgres_claim_smoke.py --database-url postgresql+psycopg://recruitment:recruitment@localhost:5432/recruitment
```

## External operations

The following scripts can contact real sites and are not routine verification. Inspect their help
and obtain approval before supplying a target or opening a session:

```powershell
.\.venv\Scripts\python.exe scripts\browser_login_capture.py --help
.\.venv\Scripts\python.exe scripts\greenhouse_read_smoke.py --help
.\.venv\Scripts\python.exe scripts\headhunter_read_smoke.py --help
```

Do not exercise apply endpoints, authenticated discovery, or submission flags as part of a normal
documentation or code check.


## Release 2.0 operations

Deploy order when a release adds migrations (the persistent database holds real data):

```powershell
docker compose exec -T postgres pg_dump -U recruitment -d recruitment > backup.sql
docker compose --profile browser build
docker compose --profile browser run --rm --no-deps -T api python -m alembic upgrade head
docker compose --profile browser up -d
docker compose exec -T api python -m alembic current   # expect 0047
```

Reports and one-off tools (all default to read-only or a dry run; run them inside the `api`
container with `PYTHONPATH=/app` or locally with `APP_DATABASE_URL`):

```powershell
python scripts/report_vacancy_duplicates.py                       # read-only duplicate report
python scripts/import_legacy_annotations.py --from-resume OLD --to-resume NEW          # dry run
python scripts/import_legacy_annotations.py --from-resume OLD --to-resume NEW --apply
python scripts/import_legacy_annotations.py --to-resume NEW --rollback
python scripts/ltr_pipeline.py train --split gold-v1              # refuses until the dataset is ready
python scripts/ltr_pipeline.py shadow --user-id U --resume-id R  # needs APP_LTR_ENABLED=true
python scripts/matching_ab.py --split gold-v1                     # replay A/B on a frozen fold
```

The browser-worker image pins Playwright to the version of its base image (`PLAYWRIGHT_VERSION` in
`Dockerfile.browser`). If a login window fails with "Executable doesn't exist", the two have drifted;
rebuild after aligning them.
