# Matching v2 development

Read this document when running or changing the matching pipeline locally.

Start the ordinary dependencies without downloading model weights:

```powershell
docker compose up -d postgres redis opensearch
.\.venv\Scripts\python.exe scripts\opensearch_smoke.py
```

The application reads `APP_MATCHING_MODEL_SERVICE_URL`, `APP_MATCHING_V2_ENABLED`,
`APP_MATCHING_V2_SHADOW_MODE`, and `APP_MATCHING_V2_FALLBACK_ENABLED`. Keep shadow mode enabled for
development unless a reviewed rollout explicitly says otherwise.

The endpoint must implement `POST /v1/embeddings` with 1024-dimensional normalized vectors and
`POST /v1/rerank` with raw and normalized scores. Unit and contract tests use deterministic fakes
and do not download model weights.

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
