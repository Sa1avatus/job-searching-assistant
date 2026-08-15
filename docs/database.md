# Database and migrations

Read this document when changing SQLAlchemy rows, persistence services, task claiming, retention, or
Alembic migrations.

## Storage roles

- PostgreSQL is the supported runtime database and authoritative business store.
- Redis holds coordination leases and transient queue signals; it is not business history.
- OpenSearch is a rebuildable matching projection. Its loss must not lose authoritative data.
- SQLite is used only by isolated tests that create tables from SQLAlchemy metadata. The historical
  Alembic chain is not required to run against SQLite.
- Resumes, screenshots, traces, and encrypted Playwright state live under the configured artifact
  root; database rows contain controlled metadata and root-confined relative paths.

ORM declarations are in `app/storage/tables.py`. Engine and session construction are in
`app/storage/database.py`. Follow the transaction and rollback behavior of the nearest service;
`session_scope()` supplies a session but does not commit automatically.

## Migration rules

The committed linear history currently runs from revision `0001` through `0034`. Confirm the actual
head instead of copying that number into a new migration:

```powershell
.\.venv\Scripts\python.exe -m alembic heads
```

Before creating a migration, inspect the latest file under `migrations/versions/` and the affected
row definitions. Keep upgrades compatible with PostgreSQL and keep metadata-created SQLite tests in
mind. Never edit or delete an applied migration to rewrite history.

Applying migrations changes persistent data and requires confirmation:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
docker compose exec -T api alembic current
```

For a schema change, verify at minimum:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_matching_tables.py -q
.\.venv\Scripts\python.exe -m pytest tests/integration/test_sql_task_repository.py -q
.\.venv\Scripts\python.exe -m alembic heads
git diff --check
```

Choose the focused table or migration test that matches the change rather than running unrelated
examples mechanically.

## Important invariants

- Applications remain unique for a user and vacancy; task and matching reruns are idempotent.
- Durable task claims use PostgreSQL locking and are separated by queue; ordinary dispatchers do not
  claim browser-only work.
- Do not hold database sessions open while external browser or model work executes unless the
  surrounding implementation explicitly requires it.
- Persist a checkpoint before an irreversible external action and confirm completion before retrying.
- User, resume, vacancy, and application ownership filters must remain explicit on reads and writes.
- File deletion must resolve paths under the configured artifact root before touching the filesystem.
- Schema changes involving personal data need retention, deletion, encryption, and API exposure
  review in the same task.

Matching-specific tables and index ownership are described in `matching-data-model.md`.
