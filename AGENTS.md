# Job Searching Assistant agent guide

## Purpose

This repository is a review-first recruitment assistant built with FastAPI, SQLAlchemy/Alembic,
PostgreSQL, Redis, OpenSearch, LLM providers, and Playwright. It stores personal employment data and
can use authenticated browser sessions or submit real applications when explicitly enabled.

## Commands

```powershell
# Local development setup and API
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload

# Safe verification
.\.venv\Scripts\python.exe -m pytest tests/unit/test_policy.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff format --check app adapters tests migrations scripts
.\.venv\Scripts\python.exe -m ruff check app adapters tests migrations scripts
.\.venv\Scripts\python.exe -m mypy app adapters scripts
.\.venv\Scripts\python.exe -m alembic heads
git diff --check

# Docker lifecycle
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
docker compose --profile browser ps
docker compose down
```

`setup.ps1` builds images, creates local configuration and encryption material, starts persistent
services, and may download images; ask before running it. Applying migrations to a persistent
database (`python -m alembic upgrade head`) also requires confirmation.

## Repository map

- `app/domain/` — policy and typed domain values; keep it independent of adapters and storage.
- `app/services/` and `app/workflows/` — application use cases and durable state transitions.
- `app/api/` and `app/static/` — HTTP contracts and the local dashboard/review UI.
- `app/storage/` and `migrations/` — SQLAlchemy persistence and the linear Alembic history. Read
  `docs/database.md` before changing either.
- `app/browser/`, `app/workers/`, and `adapters/job_boards/` — browser execution, queues, and site
  integrations. Read `docs/browser-automation.md` first.
- `app/matching/`, `ml_service/`, and `evaluation/` — explainable matching. Start with
  `docs/matching-architecture.md`.
- `tests/` and `fixtures/` — controlled tests and local browser pages. See `docs/testing.md`.

## Context routing

| Work | Read first |
| --- | --- |
| Product scope or external behavior | `docs/product-scope.md`, then `README.md` |
| Service boundaries or data flow | `docs/architecture.md` and relevant ADR |
| Database models or migrations | `docs/database.md` |
| Browser sessions, adapters, or submission | `docs/browser-automation.md`, `docs/security.md` |
| Universal site workflows/autofill | `docs/universal-site-automation.md`, ADR 0003 |
| Matching | `docs/matching-architecture.md`, then the matching topic document |
| Setup, commands, or failures | `docs/commands.md`, `docs/known-limitations.md` |
| Tests | `docs/testing.md` |

## Change workflow

Find the nearest implementation and focused test, preserve layer boundaries, make the smallest
coherent change, update behavior tests and documentation when needed, run relevant checks, and
review the diff. Do not create a plan document for routine work.

## Local Code Worker discipline

- Keep the task envelope at `D:\OpenAIProjects\tasks\current.json` and set
  `"prompt_format": "xml"`; the Worker converts that JSON task into the model-facing XML Execution
  Contract.
- Do not switch to `"prompt_format": "json"` to compensate for invalid code, imports, formatting,
  JSON fields, or unified diffs. JSON mode requires a documented compatibility need and explicit
  user approval for the mode change.
- Obtain separate explicit approval in the current chat before every provider generation, including
  corrections and recovery attempts. Autonomous continuation is not approval for another model
  call.
- Stop after three model-output failures for the same objective. Invalid JSON, missing fields,
  malformed patches, and `repair_failed` runs count. Do not reset the count by changing task IDs,
  proposal formats, prompt formats, or file boundaries. Implement only the narrow fallback directly
  after the third failure.
- Do not make intermediate commits to satisfy Worker cleanliness or read-only-context validation.
  Staging, committing, pushing, branching, or checkout always requires an explicit current user
  request; preserve the user's existing five modified files and all other unrelated work.
- Proposal generation and proposal application are separate approvals. Never apply a proposal just
  because generation was approved.

## Boundaries

### Always

- Ground generated materials and form answers only in verified user data.
- Keep external actions deterministic, typed, auditable, and reviewable.
- Preserve the review checkpoint and test with controlled fixtures when changing browser behavior.
- Keep PostgreSQL authoritative; Redis is coordination state and OpenSearch is rebuildable.

### Ask first

- Public API changes, runtime dependencies, migrations, authentication, CI, or Docker topology.
- Live provider calls, authenticated browser sessions, external-site smoke tests, or feature flags
  that enable submission.

### Never

- Submit a real application from tests or verification.
- Automate CAPTCHA, bypass platform controls, execute arbitrary page JavaScript, or follow an
  unapproved host.
- Log or commit resumes, profile data, credentials, cookies, browser state, evidence, or `.env`.
- Delete persistent volumes, downgrade applied migrations, or claim an unexecuted integration works.
