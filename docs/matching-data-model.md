# Matching v2 data model

- `vacancy_requirements`: versioned atomic requirements with importance, blocker status, weight,
  alternatives, confidence, and an exact source fragment.
- `candidate_evidence`: versioned CV evidence with experience level, verification state, confidence,
  and an exact source fragment.
- `embedding_records`: PostgreSQL metadata for each indexed evidence vector. Vector values live only
  in the rebuildable OpenSearch projection.
- `requirement_matches`: lexical, dense, hybrid, raw reranker, normalized reranker, final
  requirement score, explanation, and model provenance.
- `application_match_results`: aggregate component scores, eligibility, counters, run status,
  failure/fallback diagnostics, model versions, scoring version, and timestamps.

`applications.match_score` remains intact during migration. In shadow mode it remains the
user-visible legacy score. When v2 becomes primary, the deterministic final score is synchronized
back to that field for frontend compatibility.

Extraction run IDs are deterministic over entity ID, source hash, model, model version, and schema
version. Workflow task keys additionally bind an application to the selected CV and vacancy content,
so retries do not create duplicate business rows.
