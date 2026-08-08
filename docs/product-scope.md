# Product scope and policy

Read this document when changing user-visible behavior, connector capabilities, model-generated
content, review policy, or the boundary between preparation and submission.

## Product goal

Job Searching Assistant helps one user discover vacancies, compare them with reviewed resume data,
prepare truthful application materials, and execute supported browser steps under explicit policy.
It is a local recruitment assistant, not an unattended bulk-application or scraping platform.

## Current product surfaces

- A local dashboard and detailed review queue.
- Resume upload, text extraction, reviewed profile facts, and resume selection.
- Vacancy import and discovery for HeadHunter, LinkedIn, and Greenhouse within the limitations in
  `adapter-guide.md`.
- Legacy and matching-v2 scores, explainable evidence, and optional shadow evaluation.
- Drafted cover letters and screening answers from configured LLM providers.
- Durable tasks, human-action checkpoints, worker heartbeats, evidence, and audited retries.
- Encrypted browser sessions, per-user model preferences, canonical autofill values, arbitrary site
  definitions, field mappings, and encrypted overrides.

The closed declarative browser workflow is only partially implemented. See
`universal-site-automation.md` for the exact boundary.

## Product invariants

### Truthfulness

Application content must be grounded in reviewed resume data or explicit user-provided facts. Do not
upgrade conceptual familiarity to hands-on experience or invent employment, education,
certification, authorization, salary, language, or project claims. Unknown required facts stop for
input or review.

### Human control

Review is the default submission mode. CAPTCHA, 2FA, legal declarations, protected demographic or
medical questions, background checks, ambiguous work authorization, broad consent, payment, and
unknown required answers always stop for the user.

HeadHunter and LinkedIn submission paths are opt-in and source-specific. Enabling a feature flag is
not permission to exercise a real account during development or verification.

### Deterministic execution

LLM output is validated into typed data before it can influence a workflow. Models never receive
direct Playwright, SQL, shell, or arbitrary filesystem control. Browser actions use allowlisted
hosts, bounded retries, persisted checkpoints, and evidence.

### Data ownership

PostgreSQL is the authoritative business store. Redis coordinates work and leases. OpenSearch holds
a rebuildable search projection. Browser state and file artifacts remain root-confined outside the
database and are referenced by metadata.

### Platform boundaries

Do not bypass CAPTCHA, anti-abuse controls, rate limits, authentication, or site restrictions. Do
not add stealth, fingerprint evasion, proxy rotation, fake identities, or automated collection of
private data. LinkedIn automation carries an explicit account-restriction risk documented in
`known-limitations.md`.

## Out of scope until deliberately implemented

- unrestricted automation of arbitrary websites;
- arbitrary JavaScript or generated browser code;
- unattended handling of sensitive declarations;
- automatic promotion of model prompts or matching algorithms from small samples;
- production deployment, Kubernetes, or a distributed multi-machine control plane;
- claims of live connector compatibility without a dated, authorized exercise.

Roadmap ideas belong in the issue tracker or `backlog.json`, not in persistent agent instructions.
