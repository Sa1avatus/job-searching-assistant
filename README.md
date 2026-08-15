# Job Searching Assistant

[Русский](README.ru.md) | **English**

Job Searching Assistant is a local, review-first recruitment platform. It imports and discovers
vacancies, analyses resumes, calculates explainable matches, drafts application materials, and
prepares supported browser forms for human review. Real HeadHunter and LinkedIn submission is
disabled by default and requires explicit configuration and user confirmation.

Current version: **1.5.3**.

## Current release

- Matching recalculation is split into a fast **smart recalculation** (reuses cached LLM
  results; unchanged content returns immediately) and a **full recalculation** for after
  changing the LLM model. Decomposition/entailment results are cached in Redis with
  model-aware keys, entailment stops early after a strong confirmation, entailment pairs
  are evaluated in **batched LLM calls** (5 pairs per call by default), and simple skills
  skip the LLM entirely — cutting cold-run time and making repeated runs near-instant.
  Entailment classification can additionally be routed to a smaller local model via
  `APP_MATCHING_ENTAILMENT_MODEL` (e.g. `qwen3:1.5b`) while extraction and decomposition
  keep using the model selected in the Model tab.

- Matching v2.3 decomposes vacancy requirements into claims and distinguishes confirmed,
  partial, insufficient, failed, and missing evidence.
- Application materials receive the same vacancy key skills shown on the vacancy card. A skill is
  emphasized only when it is also present in verified candidate facts; vacancy requirements are
  never turned into candidate claims automatically.
- Optional RAG search and profile, resume, and vacancy ingestion are scoped by the owning user.
- **Direct to reranker** waits for owner-scoped RAG ingestion and detailed matching before showing
  search results. Available external RAG and reranker services refine evidence ordering; unavailable
  services fall back to local hybrid matching without discarding the discovered vacancy.
- Profile facts can be imported from supported files or extracted from a selected resume and
  reviewed before they affect matching or generated text.

See [`CHANGELOG.md`](CHANGELOG.md) for the complete version history.

## What is included

- FastAPI dashboard and review queue;
- PostgreSQL as the business-data source of truth;
- Redis-backed task coordination and worker leases;
- a rebuildable OpenSearch matching index;
- isolated Playwright sessions for HeadHunter, LinkedIn, Greenhouse, and configured sites;
- user-selected Anthropic, Gemini, or OpenAI-compatible material generation;
- encrypted browser state, LLM keys, and sensitive autofill values;
- controlled fixtures for browser and end-to-end verification.

The dashboard is available locally at `http://127.0.0.1:8000/dashboard` and on the trusted LAN at
`http://192.168.1.93:8000/dashboard`; the corresponding review pages use `/review`. Compose exposes
only port `8000` to the LAN. PostgreSQL, Redis, OpenSearch, the browser desktop, browser worker, and
matching-model ports remain bound to loopback. LAN access runs the API in production mode and
requires `APP_API_KEY` or `APP_API_CLIENTS_JSON`; `scripts/setup.ps1` generates `APP_API_KEY` in the
ignored local `.env` when it is missing.

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

### Manual email import

The **All vacancies** panel can import application correspondence without connecting an IMAP
mailbox. Select up to 100 `.eml` files, an `.mbox` mailbox export, ZIP archives containing EML
files, or a mixture of these formats. The combined upload is limited to 50 MB and processing is
bounded to 500 messages per import. Attached EML messages are classified separately, so a forwarded
or exported rejection can update the matching application without its text being merged into the
outer message. Plain-text and HTML-only bodies are supported; the sender display name may be used as
a conservative company hint when linking a message. The completion summary distinguishes unknown
messages from recognized messages that could not be linked safely. Uploaded source files are
processed in memory (with a temporary file only while reading mbox data) and are not retained by the
application.

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

### RAG collections

JSA keeps different business objects in separate owner-scoped RAG collections:

- `profiles` — reviewed profile facts;
- `resumes` — analysed resume summaries, skills, and search keywords;
- `vacancies` — vacancy descriptions and attributes.

The collections must exist in the RAG project and be authorized for JSA's service API key before
ingestion. A reviewed resume is synchronized after its analysed profile is confirmed, and reviewed
profile facts are synchronized after creation, editing, deletion, or batch confirmation. Existing
documents are updated with optimistic locking. Fact imports also refresh the reviewed profile after
persistence. Deleting a resume, the last verified profile fact, or the owning user removes the
corresponding owner-scoped RAG documents. **Direct to reranker** performs another refresh before
matching. Resume synchronization runs through the durable dispatcher with bounded retries. Its
current state, attempt count, last safe failure code, and successful synchronization time are
returned with each resume and shown in the resume panel.

The resume panel also provides **Send to RAG** for an explicit durable retry of one confirmed
resume. The action is rejected when the resume has not been confirmed or RAG is not configured,
does not duplicate an active task, and never changes the authoritative local resume record on
synchronization failure.

Existing records can be synchronized in bounded owner-scoped batches:

```powershell
recruitment-assistant rag-backfill --user-id USER_ID --batch-size 50
```

While either `resumes_has_more` or `vacancies_has_more` is true, pass both returned cursors to
`--after-resume-id` and `--after-vacancy-id` in the next invocation. The command is idempotent and
prints counts, continuation flags, and cursors only; it never prints profile or resume content.
Running it contacts the configured RAG service and should be an explicit operator action.
Aggregate `rag_sync_*` and `rag_delete_*` counters are available on the existing `/metrics`
endpoint. They contain counts only and no document content or identifiers.

Legacy matching explanations can be scheduled for replacement in bounded owner-scoped pages:

```powershell
recruitment-assistant matching-backfill --user-id USER_ID --batch-size 50
```

Pass `next_application_cursor` as `--after-application-id` while `has_more` is true. Only stale,
failed, or pre-source-v2 results are scheduled; current results and applications without an
existing detailed result are skipped. The previous score and explanation remain readable until the
replacement calculation succeeds.

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
