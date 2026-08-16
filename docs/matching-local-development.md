# Matching v2 development

Read this document when running or changing the matching pipeline locally.

Start the ordinary dependencies without downloading model weights:

```powershell
docker compose up -d postgres redis opensearch
.\.venv\Scripts\python.exe scripts\opensearch_smoke.py
```

The application reads `APP_EMBEDDING_SERVICE_URL`, `APP_RERANKER_SERVICE_URL`,
`APP_RERANKER_API_KEY`, `APP_MATCHING_V2_ENABLED`, `APP_MATCHING_V2_SHADOW_MODE`, and
`APP_MATCHING_V2_FALLBACK_ENABLED`. Keep shadow mode enabled for development unless a reviewed
rollout explicitly says otherwise. `APP_MATCHING_MODEL_SERVICE_URL` remains an embeddings-only
migration fallback when `APP_EMBEDDING_SERVICE_URL` is not set.

The embedding endpoint must implement `POST /v1/embeddings` with 1024-dimensional normalized
vectors. The independent reranker implements `POST /v1/rerank` with the public `query + documents`
contract and requires `Authorization: Bearer <RERANKER_API_KEY>`. JSA uses the returned probability
score as both the legacy raw score and normalized score. It maps results by evidence ID rather than
response position. Unit and contract tests use deterministic fakes and do not download model
weights.

When JSA runs directly on the host, a typical reranker URL is `http://localhost:8200`. From the JSA
Compose containers on Docker Desktop, configure `APP_RERANKER_SERVICE_URL` as
`http://host.docker.internal:8200`. Use the reranker's service API key, not its admin token. When
either the URL or key is absent, JSA does not contact a guessed endpoint and conservatively falls
back to normalized hybrid retrieval scores.

JSA exposes `GET /api/v1/admin/reranker/status` for a sanitized operator view. The probe uses the
reranker's public `/health/live` and `/health/ready` endpoints, then calls `/v1/models/current`
with the service bearer token. It returns controlled disabled/degraded states with HTTP 200 and
does not make the main JSA `/ready` endpoint depend on the optional reranker.

Relevant checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_matching_pipeline.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit/test_matching_scoring.py -q
.\.venv\Scripts\python.exe -m ruff check app\matching tests\unit\test_matching_pipeline.py tests\unit\test_matching_scoring.py
.\.venv\Scripts\python.exe -m mypy app\matching
docker compose config --quiet
```

The optional `matching-models` Compose profile downloads large model artifacts and requires a
compatible NVIDIA runtime. Starting it is an external/resource-heavy action and requires approval.
The independent `reranker-service` is not started by JSA Compose. A real integration smoke test
requires separate approval and must verify its readiness and bearer-authenticated public endpoint.
