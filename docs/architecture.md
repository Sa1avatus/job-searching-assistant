# Architecture

The initial platform is a layered modular monolith.

1. `app/domain` owns verified facts, matching, submission policy, and task states.
2. `app/workflows` coordinates deterministic state changes.
3. `app/storage` implements PostgreSQL persistence through SQLAlchemy and Alembic.
4. `app/browser` performs typed Playwright actions and returns structured evidence.
5. `app/api` exposes validated HTTP contracts.
6. `app/workers` uses Redis ownership leases and conservative per-domain rate decisions.
7. `app/prompts` loads validated, versioned prompt definitions independently of model providers.
8. `app/tools` validates inputs/outputs and enforces scopes/timeouts before deterministic invocation.
9. `app/workers/retention` periodically removes expired CV rows/files together, then purges other
   expired artifacts while excluding the database-managed document directory.
10. `app/workers/dispatcher` atomically claims scheduled SQL tasks with `FOR UPDATE SKIP LOCKED`,
    acquires a Redis execution lease, records bounded retries and state transitions, and publishes a
    persistent heartbeat. Database sessions are not held while a handler executes.
11. `app/workers/browser_worker` owns the Chromium runtime, audits restartable browser sessions, and
    claims only tasks assigned to the durable `browser` queue. Typed task payloads permit the
    allowlisted controlled workflow but reject arbitrary target URLs. Playwright cookies and origin
    storage are serialized outside PostgreSQL with Fernet authenticated encryption;
    `browser_sessions` contains only lifecycle metadata and a root-confined path.

The Compose API image remains small and does not bundle Chromium. The opt-in `browser` Compose
profile builds a dedicated image from `Dockerfile.browser`; the worker requires an encryption key
and reports a persistent health/degraded heartbeat after authenticating recoverable state files.
The restart smoke closes Chromium, reloads encrypted state, and verifies cookie/localStorage recovery.

Form discovery prefers accessible labels and context, groups related radio controls, and normalizes
HTML length/range/pattern/file constraints into domain values. `validate_field_answer` runs before
the generic browser fill action; invalid or unsupported values never reach the page.

Selector recovery is deterministic and bounded. It tries an active adapter mapping, accessible
label, placeholder, and stable ID up to the configured attempt cap. A fallback is promoted only
after a real action succeeds; prior versions remain inactive but reversible in root-confined JSON.
Corrupted selector metadata falls back to ordinary semantic resolution instead of blocking work.

Generated or model-provided text cannot call Playwright or persistence directly. It must first be
validated into domain types, after which deterministic policy decides whether review or execution is
allowed. A workflow checkpoint is persisted before any future irreversible action.

The current controlled path is: assessment → policy decision → durable task → browser fill → review
checkpoint. `ApplicationReviewWorkflow` persists the running state before browser work and persists
the screenshot-backed `waiting_for_user` checkpoint afterward. Final submission is intentionally
absent until approval and duplicate-verification APIs are implemented and tested.

Resume-derived profile data is scoped to `cv_files`, not to the user as a single global profile.
`users.active_cv_file_id` selects the default resume, while every discovery request may explicitly
provide a `cv_file_id`. Matching, search keywords, generated materials, and the selected application
document are therefore grounded in the same resume. Verified non-resume facts such as contact and
authorization data remain user-scoped. Legacy global skill facts remain a compatibility fallback
only when no analyzed resume profile is selected.

For background execution, the SQL row carries its queue and validated payload. The ordinary
dispatcher cannot claim `browser` work; the isolated Chromium worker claims it atomically and
persists screenshot evidence plus `submission=false` when it reaches the review boundary.

Greenhouse applications enter that queue only through an explicit review API action. The API
requires all mandatory stored answers, a managed CV when required, and explicit human provenance
for supplied sensitive answers. The task payload contains only the workflow discriminator; the
worker reloads the URL, answers, and document path from PostgreSQL, revalidates the exact Greenhouse
HTTPS host and root-confines the CV path. Redirects away from trusted Greenhouse hosts fail closed.
When Chromium reaches review, the dispatcher stores the task transition, human-action checkpoint,
and validated root-confined PNG metadata in one database transaction. The review UI loads that
image only through the scoped, no-store evidence endpoint. Application-review checkpoints cannot
be resumed into another browser loop; approve, reject, or skip resolves the checkpoint and task.
