# Adapter guide

## Greenhouse

The Greenhouse adapter supports current public job URLs in this form:

```text
https://job-boards.greenhouse.io/{board_token}/jobs/{job_id}
https://boards.greenhouse.io/{board_token}/jobs/{job_id}
```

It converts the URL to the documented public Job Board API endpoint and performs only:

```text
GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}
    ?questions=true&pay_transparency=true
```

The implementation validates the hostname, board token, and numeric job ID before constructing the
API URL. It extracts normalized vacancy text, location, deadline, application fields, options, and
compliance signals. Compliance, demographic, or GDPR data always sets the human-review boundary.

For review-mode browser preparation, `GreenhouseAdapter.prepare_review` validates the exact host,
discovers accessible fields, rejects answers for unknown field IDs, validates required values and
HTML constraints, fills through semantic locators, and captures a screenshot checkpoint. It never
interacts with a submit control. This behavior is verified against a controlled local ATS fixture;
external form filling is not claimed as live-verified.

Application submission is deliberately not implemented. Greenhouse POST requires a secret Job Board
API key and would be irreversible; enabling it requires explicit source authorization, duplicate
verification, server-side secret handling, and dedicated non-production acceptance tests.

Reference: [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html).

## HeadHunter (hh.ru) — browser-based, not api.hh.ru

**This adapter no longer calls `api.hh.ru`.** The anonymous JSON API is CAPTCHA-limited in
practice, so `adapters/job_boards/headhunter_browser.py` reads hh.ru's public pages directly
through a real (optionally headless) Chromium instance instead, for both search and single-vacancy
extraction:

- `HeadHunterBrowserAdapter.search(text, location_names, limit)` — loads
  `https://hh.ru/search/vacancy?text=...&area=...` and scrapes result cards via `data-qa`
  selectors. Location names are resolved to hh.ru's numeric `area` ids through a small built-in
  table (`KNOWN_AREA_IDS`) rather than an HTTP lookup — extend that table for regions you search
  often; unknown names are ignored (search falls back to "anywhere").
- `HeadHunterBrowserAdapter.extract_vacancy(url)` — loads the vacancy page and scrapes title,
  company, location, description, key skills, and whether a cover letter is required.
- Neither method requires a captured login session — these are public pages. Only
  `HeadHunterBrowserAdapter.apply(...)` (real response submission) needs a session captured via
  `scripts/browser_login_capture.py headhunter`.
- A CAPTCHA/verification checkpoint raises `CaptchaChallenge`; callers (job discovery, the manual
  import endpoint) treat that as "found nothing this run" rather than a hard failure.

Selectors are based on hh.ru's `data-qa` attributes (used by the site's own UI tests, so they
change less often than most markup) but have not been exercised against the live site from the
development sandbox — verify locally with `APP_BROWSER_HEADLESS=false` before relying on this
unattended, and update the selectors in `headhunter_browser.py` if hh.ru changes its markup.

`adapters/job_boards/headhunter_api.py` (the old `api.hh.ru` HTTP client) still exists and is
still usable if you have a working OAuth token and prefer the API where it works, but nothing in
the API layer (`app/api/main.py`) calls it anymore — `import-headhunter` and
`discover-headhunter-vacancies` both go through the browser adapter.

Reference (informational only, not used at runtime): [HeadHunter OpenAPI](https://api.hh.ru/openapi/redoc).

## LinkedIn — browser-based search and Easy Apply

LinkedIn never exposed a usable job-seeker API in this project (Talent APIs require an approved
partner integration LinkedIn does not grant to individuals). `adapters/job_boards/linkedin_browser.py`
now supports real browser automation instead, all gated behind `APP_ENABLE_LINKEDIN_APPLY=true` (an
explicit, informed opt-in — LinkedIn's terms prohibit this and it actively detects/bans automation):

- `LinkedInBrowserAdapter.search(text, location_names, limit)` — loads
  `https://www.linkedin.com/jobs/search/?keywords=...&location=...` and scrapes result cards.
  **Requires a session** captured via `scripts/browser_login_capture.py linkedin` — anonymous
  LinkedIn job search hits an auth wall almost immediately, unlike hh.ru.
- `LinkedInBrowserAdapter.extract_vacancy(url)` — reads a job posting's detail pane (title,
  company, location, description, whether it has a native Easy Apply control).
- `LinkedInBrowserAdapter.apply(...)` — completes the native multi-step Easy Apply modal only.
  Jobs that redirect to an external company site are skipped (`ApplyBlocked`), both by `apply()`
  and by the job-discovery staging step, rather than following an unknown third-party form.
- CAPTCHA/verification checkpoints (`CaptchaChallenge`) and expired sessions (`LoginRequired`)
  both stop at a screenshot checkpoint for the human — never solved/refreshed automatically.

The older `adapters/job_boards/linkedin_reference.py` (manual metadata entry, no network at all)
still exists and is still the only path if you'd rather not enable LinkedIn browser automation —
`POST /v1/vacancies/import-linkedin-reference` is unaffected by any of the above.

Selectors have not been exercised against the live site from the development sandbox (no network
egress there) — verify locally before unattended use, and expect to maintain them more often than
hh.ru's; LinkedIn changes its DOM more frequently.
