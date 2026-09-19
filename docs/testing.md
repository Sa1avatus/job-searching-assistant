# Testing

Read this document when adding tests or selecting verification for a change. The default automated
suite uses controlled fixtures and must not submit applications or modify real accounts.

## Quality gates

```powershell
.\.venv\Scripts\python.exe -m ruff format --check app adapters tests migrations scripts
.\.venv\Scripts\python.exe -m ruff check app adapters tests migrations scripts
.\.venv\Scripts\python.exe -m mypy app adapters scripts
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

CI runs the same format, lint, type, and test checks, then audits `requirements.txt` and builds the
default Docker image. A local pass does not prove the remote workflow ran.

To format changed Python files, run the corresponding scoped command without `--check`; review the
resulting diff before continuing.

## Select the smallest relevant test

```powershell
# Domain policy
.\.venv\Scripts\python.exe -m pytest tests/unit/test_policy.py -q

# API contract
.\.venv\Scripts\python.exe -m pytest tests/api/test_api.py -q

# SQL task repository
.\.venv\Scripts\python.exe -m pytest tests/integration/test_sql_task_repository.py -q

# Controlled browser suite
.\.venv\Scripts\python.exe -m pytest tests/browser -q

# Controlled end-to-end form
.\.venv\Scripts\python.exe -m pytest tests/end_to_end/test_controlled_application.py -q
```

Tests are organized by behavior rather than by source-file parity. Follow the nearest fixture and
assertion style. Add regression coverage for the user-visible failure being fixed; do not broadly
mock away persistence, policy, host validation, or submission boundaries.

## Release 2.0 test areas

- Dashboard behaviour is asserted on the page *and* its linked assets: use
  `tests/unit/ui_source.py::read_ui_source` (unit) or `tests/api/ui_http.py::get_ui_page` (HTTP), never
  `read_text` on `dashboard.html` alone. `tests/api/test_ui_assets.py` guards that no inline script or
  style returns and that every referenced asset is served.
- Migrations are verified on a scratch PostgreSQL database (upgrade, downgrade one step, upgrade)
  before they touch the persistent one; SQLite tests create tables from metadata.
- Annotation, dataset-split, LTR, A/B, lifecycle, CRM and strategy tests use synthetic labelled data;
  no test claims a benchmark result. Optional LightGBM tests skip when the `ltr` extra is absent.
- Browser tests need Chromium (`python -m playwright install chromium`); without it they fail on
  launch, which is an environment problem, not a code failure.
- `tests/unit/test_browser_worker_image.py` guards the Playwright pin and the display start order.

## Database and service dependencies

Most unit/API tests create SQLite tables directly from SQLAlchemy metadata. PostgreSQL migration and
claim behavior must be checked separately when those contracts change. Docker-backed smoke tests in
`docs/commands.md` require running services and may write only local test data.

Matching unit and contract tests use deterministic fakes and do not download model weights. The
optional `matching-models` service and external LLM providers are not part of the default suite.

## Prohibited test behavior

- Do not open a real authenticated browser session.
- Do not send a real application, email, or employer message.
- Do not call paid LLM providers unless the user authorizes a live-provider test.
- Do not use production databases, delete persistent volumes, or clear user artifacts.
- Do not weaken assertions, skip checks, or hide errors to produce a passing result.
