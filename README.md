# Job Searching Assistant

[Русский](README.ru.md) | **English**

Job Searching Assistant is a local, review-first recruitment platform. It imports and discovers
vacancies, analyses resumes, calculates explainable matches, drafts application materials, and
prepares supported browser forms for human review. Real HeadHunter and LinkedIn submission is
disabled by default and requires explicit configuration and user confirmation.

Current version: **1.1.300**.

## What is included

- FastAPI dashboard and review queue;
- PostgreSQL as the business-data source of truth;
- Redis-backed task coordination and worker leases;
- a rebuildable OpenSearch matching index;
- isolated Playwright sessions for HeadHunter, LinkedIn, Greenhouse, and configured sites;
- user-selected Anthropic, Gemini, or OpenAI-compatible material generation;
- encrypted browser state, LLM keys, and sensitive autofill values;
- controlled fixtures for browser and end-to-end verification.

The dashboard is available at `http://127.0.0.1:8000/dashboard`; the detailed review queue is at
`http://127.0.0.1:8000/review`.

## Quick start on Windows

Requirements: Windows 10/11, Docker Desktop with WSL 2, Git for Windows, at least 8 GB RAM, and
10 GB free disk space.

```powershell
git clone https://github.com/Sa1avatus/job-searching-assistant.git
Set-Location job-searching-assistant
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

The setup script creates ignored local configuration, generates encryption material when missing,
ensures the shared local Worker network exists, builds the containers, applies migrations, waits for
health checks, and prints the dashboard address. It does not delete existing Docker volumes.

To stop or restart without deleting data:

```powershell
docker compose down
docker compose --profile browser up -d --wait
```

Never run `docker compose down -v` unless you intentionally want to delete local PostgreSQL, Redis,
OpenSearch, and model data.

## First use

1. Create a local user in **Access**.
2. Select an LLM provider and model in **Model** if you want resume analysis or drafted materials.
3. Upload and review one or more resumes.
4. Capture user-owned site sessions in **Site sessions** where a connector requires authentication.
5. Select a resume and start vacancy discovery.
6. Review generated data and every external action before approval.

HeadHunter dashboard discovery uses the saved user session. The lower-level read-only adapter can
also inspect public vacancy pages without a session, but this is not the dashboard workflow.
LinkedIn search requires both a captured session and `APP_ENABLE_LINKEDIN_APPLY=true`. Greenhouse
discovery reads public boards; its browser preparation stops before submission.

## Optional detailed matching

The bundled BGE model service requires an NVIDIA-capable Docker setup, at least 16 GB RAM, and
roughly 12 GB additional disk space. Its first start downloads several gigabytes:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -EnableDetailedMatching
```

Matching v2 remains in shadow mode by default. PostgreSQL keeps authoritative records; OpenSearch
can be rebuilt.

## Local development

Python 3.12 or newer is required:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

See [`docs/commands.md`](docs/commands.md) for service and diagnostic commands and
[`docs/testing.md`](docs/testing.md) for selecting safe checks.

## Documentation

- [`docs/product-scope.md`](docs/product-scope.md) — product capabilities and non-negotiable policy;
- [`docs/architecture.md`](docs/architecture.md) — component boundaries and data flow;
- [`docs/database.md`](docs/database.md) — persistence and migration rules;
- [`docs/browser-automation.md`](docs/browser-automation.md) — browser sessions and external effects;
- [`docs/security.md`](docs/security.md) — secrets, access, and personal data;
- [`docs/known-limitations.md`](docs/known-limitations.md) — verified gaps and connector risks;
- [`docs/adapter-guide.md`](docs/adapter-guide.md) — site-specific behavior;
- [`docs/matching-architecture.md`](docs/matching-architecture.md) — matching v2 entry point.

## Safety

- `.env`, browser state, resumes, screenshots, and real application evidence are local and ignored.
- Generated or model-provided text cannot directly control Playwright or persistence.
- CAPTCHA, 2FA, sensitive declarations, unknown required answers, and cross-host navigation stop for
  human review.
- Automated tests use controlled fixtures and never submit real applications.
