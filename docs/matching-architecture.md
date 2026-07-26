# Matching v2 architecture

The matching flow is:

`structured extraction → evidence indexing → hybrid retrieval → reranking → deterministic scoring`

PostgreSQL owns vacancy requirements, candidate evidence, embedding metadata, requirement matches,
and aggregate results. OpenSearch contains only a rebuildable search projection. The LLM extracts
typed, source-grounded facts but never chooses the final score.

Each application run receives a UUID and moves through `pending`, `extracting`, `indexing`,
`retrieving`, `reranking`, and `scored`. Failures become `degraded` when the legacy score is
retained, otherwise `failed`. The SQL workflow queue makes reruns idempotent by application,
selected CV content, vacancy description, and analysis timestamp.

Tenant filters (`user_id` and `cv_file_id`) are mandatory in both BM25 and kNN queries. Dense
retrieval failure falls back to BM25; OpenSearch failure falls back to a bounded PostgreSQL lexical
scan. Extraction, reranking, or model-service failure retains the old score while shadow mode is
enabled.

See [matching-data-model.md](matching-data-model.md) for persistence and
[matching-operations.md](matching-operations.md) for rollout.
