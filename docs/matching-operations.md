# Matching v2 operations

Read this runbook for controlled index rebuilds, backfills, and rollout. These commands mutate local
matching state unless marked `--dry-run`; obtain approval before non-dry-run operations.

## Rollout sequence

1. Verify PostgreSQL, Redis, OpenSearch, the dispatcher, and the configured model endpoint.
2. Confirm the Alembic head and rebuild a versioned evidence index from PostgreSQL.
3. Enable matching v2 with shadow mode still enabled.
4. Recalculate one reviewed application and inspect its match details and metrics.
5. Backfill in bounded batches with a stable resume cursor.
6. Compare legacy and v2 scores against `matching-evaluation.md`.
7. Switch shadow mode off only with explicit acceptance and a rollback decision.

The index is disposable. A rebuild writes a new physical index, validates it, and atomically moves
aliases. Failure removes only the incomplete index.

Safe discovery and dry-run commands:

```powershell
.\.venv\Scripts\python.exe scripts\matching_admin.py --help
.\.venv\Scripts\python.exe scripts\matching_admin.py recalculate-all --dry-run --limit 100 --batch-size 20
.\.venv\Scripts\python.exe scripts\matching_admin.py rebuild-index --dry-run
```

Commands that name a user, resume, or application require IDs from the local database. Obtain them
through the authenticated local API or dashboard, then follow the CLI help rather than copying a
placeholder identifier from documentation.

Changing an embedding model requires a new index version because dimensions and vector meaning are
part of the index contract. PostgreSQL records model name, revision, dimensions, normalization,
content hash, and index name.

Useful failure signals are `matching_failures_total`, `degraded_matches_total`,
`opensearch_errors_total`, stage-duration summaries, and the persisted `failure_reason`. Logs contain
identifiers and timings, not resume text.
