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

The committed linear history currently runs from revision `0001` through `0047`. Confirm the actual
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

## Vacancy identity

`vacancies` carries a canonical identity next to the raw `source_url`: `source_key`
(`headhunter`, `linkedin`, `greenhouse`, `registry`, `other`), `source_id` (posting id when the
source exposes one), `canonical_url`, and `dedup_fingerprint` (hash of normalised
company/title/location). They are computed by `app/domain/vacancy_identity.py` in a SQLAlchemy
`before_insert`/`before_update` listener, so every write path stays consistent.

- Lookups go through `app.services.vacancy_identity.find_vacancy_by_identity` (source id, then
  canonical URL, then exact URL; oldest row wins). `create_vacancy` refuses a second row for the
  same posting.
- The indexes are deliberately non-unique: databases created before migration 0040 may already
  contain duplicates that applications reference. `scripts/report_vacancy_duplicates.py` lists them;
  nothing is merged automatically. The fingerprint only groups *candidates* across sources.
- `prepare_application` raises `BlacklistedCompanyError` for a blacklisted company, so a blacklist
  blocks new applications from every ingestion path; removing the entry allows them again.

## User preferences

`user_preferences` (migration 0041) holds one row per user: minimum salary and currency, preferred
locations, work formats, and employment types. `app/domain/preferences.py` evaluates them against a
vacancy; `prepare_application` turns each demonstrated mismatch into an application warning
(`Preference mismatch (<code>): ...`). Constraints are soft by design - they never hide a vacancy
or change a score, and missing or non-comparable data (unspecified format, other currency) is never
a violation. Exposed as `GET|PUT /v1/users/{id}/preferences`.


## Migrations 0038-0047 at a glance

| Revision | Adds |
| --- | --- |
| 0038, 0039 | `annotation_feedback`; LTR shadow columns on `application_match_results` |
| 0040 | vacancy identity: `source_key`, `source_id`, `canonical_url`, `dedup_fingerprint` (backfilled, non-unique indexes) |
| 0041 | `user_preferences` |
| 0042 | `browser_session_states` |
| 0043 | annotation `pair_key`, provenance, confidence and partial unique indexes (stops on real duplicates) |
| 0044 | `annotation_splits` (freezable evaluation splits) |
| 0045 | `application_submissions` ledger (backfills confirmed rows for already-submitted applications) |
| 0046 | email explanation columns (`match_method`, `match_reason`, `review_reason`) |
| 0047 | `strategy_recommendations` |

Timeline events (`application_timeline_events`) are append-only at the ORM level. Details of each
change live in the topic documents: `docs/matching-data-model.md`, `docs/browser-automation.md`,
`docs/email-foundation.md`, `docs/architecture.md`.

