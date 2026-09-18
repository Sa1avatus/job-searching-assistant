# Matching v2 evaluation

Read this document before changing evaluation fixtures, score-promotion thresholds, or shadow-mode
rollout decisions.

Evaluation must happen in shadow mode against reviewed examples. A fixture contains vacancy text,
candidate text, expected eligibility, an expected score interval, matched and missing requirements,
and reviewer notes.

Report Precision@K, Recall@K, MRR, NDCG, score correlation, blocker precision, required-skill
coverage accuracy, latency, and failure rate. Slice results by language direction, role family,
hard blockers, and hands-on versus conceptual experience.

The minimum synthetic suite covers:

1. highly relevant RAG/LLM experience;
2. similar terminology but a different role;
3. mandatory hands-on Kubernetes;
4. conceptual-only Kubernetes;
5. Russian vacancy with English CV;
6. English vacancy with Russian CV;
7. vector-store alternatives;
8. work-authorization blocker;
9. a missing preferred skill;
10. empty or poorly extracted vacancy text.

Synthetic results validate mechanics only. Promotion out of shadow mode requires human-reviewed
production-like data, explicit quality thresholds, and a rollback plan.

## A/B replay and hybrid routing (cheap ranker first, LLM for the ambiguous zone)

Code: `app/matching/ab/` (`harness.py`, `routing.py`, `hybrid.py`), CLI `scripts/matching_ab.py`.

- **Replay harness.** Every variant ranks the same frozen human-labelled groups. The report has
  NDCG@10, Precision@10, Recall@10, MRR next to p50/p95 latency per group, items/s, CPU seconds and
  peak Python memory (tracemalloc; relative numbers for comparing variants on one machine). A variant
  that raises is counted as an error and ranked in input order, so crashing can never look better than
  skipping. Verdicts: `accept` (NDCG@10 within 0.01 of the baseline or better), `trade_off` (cheaper
  but measurably worse, or crashes), `insufficient_evidence` (fewer than 2 groups / 50 items).
- **Routing by data, not by guess.** The LLM is only useful where the cheap order is unreliable:
  around the top-k boundary. `margin_curve` replays human labels and reports, for each margin, the
  share of items that would go to the LLM and the share of the cheap ranker's boundary errors those
  items contain; `choose_margin` returns the smallest margin that captures >= 80 % of the errors
  within a 30 % LLM budget, or None (the LLM stays in charge) when the data does not support it.
- **Hybrid ranking** (`hybrid_rank`) is behind `APP_MATCHING_HYBRID_ROUTING` (default false) and
  needs `APP_MATCHING_LLM_MARGIN`. Flag off = the current pipeline for every item. The LLM's opinion
  only permutes the scores the routed items already held, so the rest of the ranking is untouched.
  Fallbacks: cheap ranker fails -> whole group to the baseline; LLM fails -> cheap order kept; the
  routed count is capped by `APP_MATCHING_LLM_MAX_SHARE`. Rollout is reversible by flipping the flag.
- **Not measured yet (honest status).** There are no human labels, so no benchmark has been run and
  no margin has been chosen; the production default is unchanged. ONNX/INT8 for the cross-encoder
  (Ettin or another) is also not measured: the repository ships no cross-encoder artifact and
  `onnxruntime` is not installed. `replay_variant` accepts any callable, so an ONNX session can be
  benchmarked with the same harness once a model is chosen; until then no claim about its speed or
  quality trade-off is made.
