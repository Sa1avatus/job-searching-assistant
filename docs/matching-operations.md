# Matching v2 operations

Apply migrations before enabling the feature. Keep shadow mode enabled until evaluation and
backfill results are accepted.

1. Verify PostgreSQL, Redis, OpenSearch, dispatcher, and the configured model endpoint.
2. Rebuild the OpenSearch evidence index from verified PostgreSQL evidence.
3. Enable `APP_MATCHING_V2_ENABLED=true` and leave `APP_MATCHING_V2_SHADOW_MODE=true`.
4. Enqueue a single application with
   `POST /v1/applications/{application_id}/recalculate-match`.
5. Inspect `GET /v1/applications/{application_id}/match-details` and `/metrics`.
6. Backfill in bounded batches; never run a full backfill during application startup.
7. Compare legacy and v2 scores before switching shadow mode off.

The index is disposable. Rebuilding creates a versioned index, writes all verified evidence, and
atomically switches read/write aliases. A failed rebuild deletes only the incomplete index.

Bounded administration commands:

```powershell
python -m scripts.matching_admin recalculate-all --dry-run --limit 100 --batch-size 20
python -m scripts.matching_admin recalculate-all --limit 100 --resume-after APPLICATION_ID
python -m scripts.matching_admin rescore-application APPLICATION_ID
python -m scripts.matching_admin reindex-user USER_ID
python -m scripts.matching_admin reindex-cv CV_FILE_ID
python -m scripts.matching_admin rebuild-index --dry-run
python -m scripts.matching_admin verify-index-consistency --user-id USER_ID
```

`extract-requirements` and `extract-evidence` use the same idempotent application queue as
`recalculate-all`; extraction is reused when its source/model/schema fingerprint is unchanged.
Every batch command supports dry-run, limit, batch size, and a stable application-ID resume cursor.
The JSON report includes the last cursor and bounded failures.

Changing an embedding model requires a new index version because dimensions and vector meaning are
part of the index contract. Model name, revision, dimensions, normalization, content hash, and index
name are recorded in PostgreSQL.

Useful failure signals are `matching_failures_total`, `degraded_matches_total`,
`opensearch_errors_total`, matching stage duration summaries, and the per-application
`failure_reason`. Logs contain IDs and timings, not CV text.
