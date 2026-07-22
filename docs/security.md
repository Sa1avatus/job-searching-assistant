# Security model

- Credentials come from environment variables; `.env` is ignored.
- Production configuration refuses to start without `APP_API_KEY` or `APP_API_CLIENTS_JSON`.
  A legacy `APP_API_KEY` is an administrator key; the JSON alternative maps multiple constant-time
  checked keys to least-privilege scopes such as `profiles:write`, `profiles:delete`,
  `vacancies:write`, `applications:write`, `review:read`, and `review:write`.
- Compose publishes API and PostgreSQL ports on loopback only.
- Automatic submission is denied for missing facts, unauthorized sources, and sensitive questions.
- Browser tests target controlled loopback fixtures only and never external accounts.
- Screenshots and task evidence are stored under `.artifacts`, which is excluded from version control.
- The browser layer does not implement CAPTCHA bypass, stealth, fingerprint evasion, or arbitrary JS.
- Persistent Playwright cookies/localStorage are stored outside the database using Fernet
  authenticated encryption. `APP_BROWSER_STATE_ENCRYPTION_KEY` is never written by the application;
  a wrong key marks the saved state corrupted.
- Browser-state reads, replacement, retention, and user deletion are root-confined. PostgreSQL stores
  only a relative encrypted-file path and nonsensitive lifecycle metadata.
- Uploaded files accept only PDF/DOCX, enforce MIME/extension/signature or ZIP structure and size,
  use generated storage names, and permit deletion only under the configured artifact root.
- Tool invocations require explicit scopes and record status-only audit events without input payloads.
- Retention cleanup resolves every path under the configured artifact root before deletion.
- External Greenhouse reads accept only an exact hostname allowlist over HTTPS on the standard port,
  reject embedded credentials, canonicalize user input, and construct the API URL from validated
  board/job identifiers rather than fetching an arbitrary supplied URL.
- CI audits resolved runtime dependencies against published vulnerability advisories.
