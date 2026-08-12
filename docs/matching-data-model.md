# Matching v2 data model

Read this document when changing matching tables, version fingerprints, OpenSearch mappings, or
result persistence. General migration rules are in `database.md`.

- `vacancy_requirements`: versioned atomic requirements with importance, blocker status, weight,
  alternatives, confidence, and an exact source fragment.
- `candidate_evidence`: versioned CV evidence with experience level, verification state, confidence,
  and an exact source fragment.
- `embedding_records`: PostgreSQL metadata for each indexed evidence vector. Vector values live only
  in the rebuildable OpenSearch projection.
- `requirement_matches`: lexical, dense, hybrid, raw reranker, normalized reranker, final
  requirement score, explanation, and model provenance.
- `application_match_results`: aggregate component scores, eligibility, counters, run status,
  failure/fallback diagnostics, model versions, scoring version, and timestamps. Component scores
  include hard/preferred skills, role, seniority, experience, work format, location, domain, and
  language. Aggregate quality signals include weighted-average semantic similarity (0–1),
  weighted-average reranker score (0–1), and requirements match ratio (0–100%). The reranker
  aggregate is 0 when no reranker scores are available.

`applications.match_score` remains intact during migration. In shadow mode it remains the
user-visible legacy score. When v2 becomes primary, the deterministic final score is synchronized
back to that field for frontend compatibility.

Extraction run IDs are deterministic over entity ID, source hash, model, model version, and schema
version. Workflow task keys additionally bind an application to the selected CV and vacancy content,
so retries do not create duplicate business rows.

The OpenSearch projection uses versioned physical indices behind configured read/write aliases.
Documents always carry `user_id`, `cv_file_id`, evidence identity, source text, controlled evidence
metadata, embedding model/revision, content hash, and index timestamp. Mapping dimensions must match
the configured model response. Changing model meaning or dimensions requires a new physical index;
do not mutate an active vector mapping in place.

Source fragments may contain resume or vacancy text. Do not log them, place them in metrics, expose
them across users, or copy them into Worker task context unless the task explicitly needs that exact
fixture and it contains no private data.
