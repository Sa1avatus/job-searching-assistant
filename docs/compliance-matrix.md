# Constitution compliance matrix

Status meanings: **verified** was executed locally; **partial** has working foundations but not the
full mandate; **missing** has no operational implementation yet.

| Sections | Capability | Status | Evidence / gap |
| --- | --- | --- | --- |
| 1–6 | Operating principles, stack, boundaries | partial | Layered core and ADR exist; complete platform does not. |
| 7 | Specialized agents | missing | Roles are not yet implemented as typed services. |
| 8–10 | Truthfulness, submission policy, compliance | verified | `domain/policy.py` tests missing, sensitive, and authorized-source cases. |
| 11 | Playwright engine | partial | Headless fill/upload/screenshot and encrypted cookie/localStorage restoration are verified; traces, downloads, network logs, and open-tab recovery remain absent. |
| 12 | ATS adapters | partial | Greenhouse public extraction is live; its explicit API→SQL→browser-worker review path, controlled Chromium fill, required-answer gate, managed-CV boundary, redirect guard, and arbitrary-URL rejection are verified. External fill is not claimed. HeadHunter official API extraction has controlled tests but live access is CAPTCHA/OAuth-limited. hh.ru and LinkedIn provide strict manual browser handoff without parsing or automated actions. |
| 13 | Form understanding | partial | Accessible DOM discovery covers every required field type, grouped radios, options, values, semantic categories and HTML constraints; deterministic pre-fill validation is enforced. Proven fallback selectors are versioned and reusable; ATS-specific schema coverage remains incomplete. |
| 14 | Durable task engine | partial | SQL scheduling/priority, typed payloads, isolated dispatcher/browser queues, atomic skip-locked claims, transition history, bounded retry, Redis execution lease, worker heartbeats, and transactional review-checkpoint/evidence completion are verified; arbitrary task DAGs remain absent. |
| 15 | Database | partial | Operational, vacancy-evidence, CV metadata, normalized grounded application answers, application selection, and task-history tables have PostgreSQL migrations; most analytics entities remain absent. |
| 16 | Memory and retrieval | missing | No full-text/vector/hybrid retrieval. |
| 17 | Model router | partial | Typed fallback, cost gate and timeout exist; provider integrations/token accounting are absent. |
| 18 | Prompt management | partial | Validated file registry, active versions and rendering exist; evaluations/A-B promotion absent. |
| 19–20 | Tools, MCP, dynamic tools | partial | Typed scoped tool registry, timeouts and audit exist; MCP transport/discovery absent. |
| 21–23 | Selector recovery, debugging, controlled evolution | partial | Evidence, typed retry policy, and bounded versioned semantic selector fallback/promotion are verified on controlled redesigns; production adapter promotion governance remains absent. |
| 24 | Machine-readable backlog | partial | Prioritized `backlog.json` exists; no automated backlog executor. |
| 25–26 | Analytics and experiments | missing | No metric persistence or experiment engine. |
| 27 | Observability | partial | DB readiness, Prometheus counters/gauges, live task/review queue depth, dispatcher/retention/browser-worker heartbeats, correlation IDs, and redacted JSON logs exist; distributed traces and error aggregation remain absent. |
| 28 | Security | partial | Least-privilege API scopes, loopback exposure, strict uploads/root-confined deletion, UUID evidence lookup with image signature/size validation and no-store delivery, dependency audit, and an HTTPS/exact-host SSRF boundary for the implemented external adapter exist. Malware scanning and a reusable SSRF boundary for future tools remain absent. |
| 29 | Privacy/retention | partial | A configurable scheduled worker synchronizes expired CV and encrypted browser-session metadata/file deletion and purges root-confined artifacts; employer communications and profile-row expiry policies remain absent. |
| 30 | Parallelism/rate control | partial | The Compose dispatcher enforces atomic SQL claims plus Redis execution leases; fixed-window domain limits are verified independently. Configurable multi-process pool sizing remains absent. |
| 31 | Resilience | partial | Task state survives restart; stale work is interrupted, handler failures use bounded delayed retries, lease conflicts reschedule safely, human-action checkpoints resume exactly once, encrypted Playwright state restores into a new context, and selector fallback is bounded/versioned. Open-tab and provider recovery remain incomplete. |
| 32–33 | Tests and quality gates | partial | Unit/API/SQL/browser/E2E, Ruff, mypy, dependency vulnerability audit, real PostgreSQL migrations, live dispatch, and container smoke tests pass; static application security scanning remains pending. |
| 34 | Documentation | partial | Setup/architecture/security/limitations exist; several mandated guides absent. |
| 35 | CI/CD | partial | CI defines formatting, lint, strict types, tests, dependency vulnerability audit, and image build; it has not been executed remotely. |
| 36 | Delivery phases | partial | Phase 1 foundation and persistent Phase 2 review queue verified; Phase 3–6 incomplete. |
| 37 | CLI UX | partial | Init/import/add/list/review and durable worker commands work; interactive progress UX remains absent. |
| 38 | Human review interface | partial | A responsive local web queue shows vacancy source, score, warnings, selected CV, workflow state, editable cover letter, grounded screening answers, missing facts, legal declarations, active human-action instructions, and on-demand protected screenshots, with approve/reject/skip, save, audited retry, one-shot resume, and explicit non-submitting Greenhouse browser preparation. |
| 39–40 | Stop conditions and execution | partial | The controlled restart-safe PostgreSQL/Redis/Chromium review flow and documentation are verified; real authenticated adapters and the broader platform remain incomplete. |

This matrix deliberately treats scaffolding as incomplete until its acceptance behavior is executed.
