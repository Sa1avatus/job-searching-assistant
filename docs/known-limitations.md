# Known limitations

Read this document when diagnosing connector behavior or deciding whether a capability has been
verified beyond controlled fixtures.

## hh.ru (HeadHunter)
- Search and vacancy extraction go through Chromium (`adapters/job_boards/headhunter_browser.py`),
  not `api.hh.ru`. The adapter can read public pages without a captured session, but dashboard
  discovery deliberately requires and reuses the candidate's saved hh.ru session. Submission also
  requires that session.
- Real response submission (`POST /v1/applications/{id}/apply-headhunter`) requires
  `APP_ENABLE_HEADHUNTER_APPLY=true` and a session captured by hand through the dashboard — the
  automation never sees or types a password.
- CAPTCHA/verification checkpoints stop at a `waiting_for_user` checkpoint with a screenshot;
  nothing is solved automatically. A CAPTCHA hit during *search* is treated as "found nothing this
  run" rather than a hard error.
- Location filtering uses a small built-in table of common region names → hh.ru area ids
  (`KNOWN_AREA_IDS`), not a live lookup; unknown region names are ignored (falls back to
  "anywhere").
- Selectors are based on hh.ru's `data-qa` attributes. Their current live compatibility is not
  established by the controlled suite and must be treated as time-sensitive.
- The vacancy's "resume" field is always reported as required by hh.ru but is never filled by this
  adapter (hh.ru attaches whichever resume is already selected in the user's own account); it is
  explicitly excluded from the pre-submission required-answer check for this reason.

## LinkedIn
- Real automation (search, vacancy extraction, and native Easy Apply submission) requires
  `APP_ENABLE_LINKEDIN_APPLY=true` — an explicit, informed opt-in, since LinkedIn's terms prohibit
  this kind of automation and the platform actively detects and bans accounts for it. This is a
  real risk the operator accepts by enabling the flag, not a theoretical one.
- Unlike hh.ru, LinkedIn job search is effectively unusable anonymously (it hits an auth wall
  almost immediately), so both search/extraction and apply require a session captured through the
  dashboard's visible sign-in window.
- Only the native, in-page Easy Apply flow is automated. Jobs that redirect to an external
  company site are still discovered and saved, but the apply step reports `ApplyBlocked` rather
  than following an unknown third-party form.
- No CAPTCHA solving, fingerprint spoofing, or proxy rotation is implemented or planned — a
  verification checkpoint always stops at a human review checkpoint with a screenshot.
- `adapters/job_boards/linkedin_reference.py` (manual metadata entry, zero network calls) still
  exists as a lower-risk alternative for anyone who prefers not to enable LinkedIn automation;
  `POST /v1/vacancies/import-linkedin-reference` is unaffected by the above.
- Search and extraction were exercised against the live July 2026 interface. LinkedIn changes its
  DOM more often than hh.ru or Greenhouse, so the adapter stores screenshot/HTML evidence when it
  can no longer parse result identifiers or a vacancy title.

## Greenhouse
- Real public read-only adapter (`api.greenhouse.io`); no external submission is enabled.
- Discovery searches explicitly entered company boards or board tokens recovered from saved
  Greenhouse vacancies. Greenhouse does not provide a global cross-company vacancy search.
- Browser preparation is connected end-to-end through the durable queue and review UI, but only
  controlled Chromium form filling and a live no-network boundary smoke are verified. A real
  external Greenhouse form has not been filled, and production subresource egress filtering is
  still required before broader unattended use.
- The current browser flow fills a controlled fixture and stops at review; it never submits.

## Materials generation (cover letters / screening answers)
- Requires a user-configured Anthropic, Gemini, or OpenAI-compatible provider. External providers
  receive the selected resume facts and vacancy context and may bill the user's account.
- The model only ever sees the candidate's own *verified* profile facts and the vacancy's own
  text; it is instructed not to invent anything beyond that, and sensitive-category fields (work
  authorization, disability, background checks, etc.) are never sent to the model and never
  written by it, even if it tries to answer them anyway (enforced in code, not just by prompting).
- Resume analysis (skill/summary extraction) is a two-step, human-confirmed flow: nothing from
  `extract-profile` is saved as a fact until the person explicitly confirms it via
  `confirm-profile-facts`. Unconfirmed drafts cannot affect vacancy matching, screening answers,
  or cover letters, since all of those only ever look at `is_verified=True` facts.
- Generated cover letters/answers land as `llm_generated` on a still-`awaiting_review`
  application and never auto-submit; a human can edit or clear them via
  `PATCH /v1/applications/{id}/materials` before any apply step.
- Vacancy title and description are inspected before drafting. Russian-language vacancies require
  a Russian cover letter; a wrong-language model response is retried once and rejected if it is
  still not Russian.

## General
- Matching v2 requires separately configured embedding and reranker services. The independent
  reranker has not been claimed as live-compatible until its bearer-authenticated Docker smoke test
  is explicitly run; when it is absent or unavailable, matching uses conservative hybrid scores.
- Human-action/CAPTCHA checkpoints and encrypted Playwright storage state are durable. The worker
  does not yet reconnect an open tab, preserve in-memory JavaScript state, or automatically resume
  an external action; recovery starts a new context from cookies/localStorage.
- Resume text extraction handles PDF, DOCX, DOC, TXT, RTF, ODT, HTML/HTM, and Markdown. Legacy DOC
  recovery is best-effort; scanned/image-only files still require OCR and are rejected when they
  yield too little usable text.
- The personal dashboard (`/dashboard`) drives hh.ru, LinkedIn, and Greenhouse discovery. LinkedIn
  search still requires an explicitly enabled connector and a captured signed-in session.
- Starting a visible sign-in browser from the dashboard is intentionally restricted to local,
  non-production installations. An unfinished sign-in window is held in API process memory and
  must be started again if that process restarts; already saved encrypted sessions remain durable.
