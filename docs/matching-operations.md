# Matching v2 operations

Read this runbook for controlled index rebuilds, backfills, and rollout. These commands mutate local
matching state unless marked `--dry-run`; obtain approval before non-dry-run operations.

## Rollout sequence

1. Verify PostgreSQL, Redis, OpenSearch, the dispatcher, the matching worker, and the configured
   model endpoint.
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

Interactive matching runs in the dedicated `matching` queue at priority 100. Retries use priority 20
with exponential backoff (30s, 60s, 120s, 240s, capped at 300s). Backfills and discovery enrichment
use priority 10. `APP_MATCHING_WORKER_CONCURRENCY=2` runs two application calculations concurrently;
both slots share one Ollama model process.

## Ollama configuration (host)

On a 12 GB GPU (RTX 3060), configure host Ollama with:

```text
OLLAMA_NUM_PARALLEL=2
OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_CONTEXT_LENGTH=8192
OLLAMA_MAX_QUEUE=16
OLLAMA_KEEP_ALIVE=10m
OLLAMA_FLASH_ATTENTION=1
```

Optionally `OLLAMA_KV_CACHE_TYPE=q8_0` to reduce VRAM pressure. `MAX_LOADED_MODELS=1` prevents an
old 8B model from staying resident alongside the selected 4B model. `NUM_PARALLEL=2` lets both
matching-worker slots share one resident model. Ollama defaults to a 512-request queue — unnecessary
when the application has its own durable queue.

After starting, verify with `ollama ps`: one model, `100% GPU`, VRAM ≤ 11.5 GB, no 503 errors.

The worker renews both its SQL claim heartbeat and Redis lease during long inference calls. Matching
tasks use a 300s lease (vs 120s for the general dispatcher) to accommodate 5–10 minute runs.

Legacy backlog migration is inspectable and non-destructive:

```powershell
docker compose run --rm matching-worker python -m app.matching.queue_repair
docker compose run --rm matching-worker python -m app.matching.queue_repair --apply
```

The first command is a dry run. The applied repair preserves task rows and transition history,
migrates only the newest active request, and terminalizes superseded legacy work.

## Queue control (dashboard)

The dashboard "Очередь матчинга" panel (backed by `app/matching/queue_admin.py`) manages the durable
`matching` queue without restarting the worker:

- `GET /v1/matching/queue` — pause state, per-state task counts, and the active task list.
- `POST /v1/matching/queue/pause` — set the `recruitment:matching:queue:paused` Redis flag; the
  worker stops claiming new tasks (already-running tasks finish).
- `POST /v1/matching/queue/resume` — clear the flag and resume claiming.
- `POST /v1/matching/queue/clear` — pause the queue, cancel every `pending`/`scheduled`/
  `retry_scheduled` task, interrupt orphaned `running` tasks, and reset their application match
  aggregates to `failed` so cards do not hang in "processing". Task rows and transition history are
  preserved.

Clearing is the safe way to stop a runaway recalculation backlog (for example after an OOM-killed
worker left hundreds of `retry_scheduled` tasks): it terminates the backlog without deleting rows,
and the pause flag keeps the queue from refilling until you resume.
