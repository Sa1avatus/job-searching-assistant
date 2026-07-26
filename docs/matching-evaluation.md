# Matching v2 evaluation

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
