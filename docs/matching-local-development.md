# Matching v2 development

Start the ordinary dependencies without model containers:

```powershell
docker compose up -d postgres redis opensearch
python -m scripts.opensearch_smoke
```

Configure an approved HTTP embedding/reranking endpoint:

```text
APP_MATCHING_MODEL_SERVICE_URL=https://matching-models.example.internal
APP_MATCHING_V2_ENABLED=true
APP_MATCHING_V2_SHADOW_MODE=true
APP_MATCHING_V2_FALLBACK_ENABLED=true
```

The endpoint must implement `POST /v1/embeddings`, returning 1024-dimensional normalized vectors,
and `POST /v1/rerank`, returning raw and normalized scores. Unit and contract tests use deterministic
fakes and do not download model weights.

Run checks:

```powershell
python -m pytest
python -m ruff check .
python -m mypy app
docker compose config --quiet
```

The optional `matching-models` profile exists for isolated compatibility testing but is disabled by
default. It is not required when using a hosted service.
