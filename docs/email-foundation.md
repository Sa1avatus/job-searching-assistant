# Email foundation

Read this document when changing email classification, event processing, application status
updates, or file-based email import.

## Components

- `app/domain/application_email.py` defines `ApplicationEmailOutcome` (REJECTED, NEXT_STAGE,
  OFFER, UNKNOWN), `EmailCategory` (10 categories from APPLICATION_RECEIVED to OTHER),
  `outcome_for_category()`, and `status_for_category()` (maps a category to the application
  status it advances to).
- `app/domain/application_status.py` defines `ApplicationStatus` (which includes the
  `needs_review` status), `TERMINAL_STATUSES`, and `ACTIVE_STATUSES`.
- `app/services/application_email_classifier.py` provides the deterministic regex classifier
  (`classify_email_category()`), used as the fallback when no LLM is configured.
- `app/services/email_classification.py` provides `EmailClassifier` — the LLM classifier that
  returns a category, confidence, extracted company/vacancy title, and a one-line summary via
  the model router (`classify_application_email` prompt).
- `app/services/email_vacancy_matcher.py` provides `EmailVacancyMatcher` — semantic retrieval
  against the rag-platform `vacancies` collection, mapped back to the user's applications.
- `app/services/application_email_events.py` provides `ApplicationEmailEventService` with
  `ingest()` (deterministic fallback), `ingest_async()` (RAG + LLM), `resolve()` (link/dismiss
  review items), and `list_review_items()` (emails needing human review).
- `app/services/application_email_sync.py` provides `ApplicationEmailSyncService` for batch
  processing from any `ApplicationEmailProvider`.
- `app/services/imap_email_provider.py` implements `ApplicationEmailProvider` for IMAP.
- `app/services/email_file_import.py` provides `EmlImportProvider`, `MboxImportProvider`, and
  `ZipEmlImportProvider` for batch file-based import.
- `app/storage/tables.py` defines `ApplicationEmailEventRow` with message fingerprint, category,
  confidence, subject/body (for the review queue), `needs_review`, candidate vacancies,
  `resolved`, and `previous_status` columns.

## Ingestion flow

Each fetched message is fingerprinted (SHA-256 of normalized subject + body) for idempotent
deduplication, then processed through the following decision:

1. **Classify** — the LLM returns a `category` + `confidence` (falling back to the regex
   classifier when the LLM is unavailable or fails).
2. **Match** — resolve the application in order of signal strength: exact normalized
   company/title, unique title/company substring, then semantic RAG retrieval over the
   `vacancies` collection (a clear winner requires the top score above a minimum and a margin
   over the runner-up).
3. **Decide** — if the category maps to a status, the classification is confident enough, and the
   match is unambiguous, the status is applied automatically. Otherwise the event is flagged
   `needs_review`, the best-guess application is set to `needs_review`, and the candidate
   vacancies are stored for the review queue.

## Email categories → outcome / status

| Category | Outcome | Status update |
|---|---|---|
| APPLICATION_RECEIVED | UNKNOWN | → approved ("Принята") |
| RECRUITER_CONTACT | UNKNOWN | none |
| QUESTION | UNKNOWN | none |
| TEST_ASSIGNMENT | NEXT_STAGE | → interview |
| INTERVIEW_INVITATION | NEXT_STAGE | → interview |
| INTERVIEW_RESCHEDULE | NEXT_STAGE | → interview |
| OFFER | OFFER | → offer |
| REJECTION | REJECTED | → employer_rejected |
| FOLLOW_UP | UNKNOWN | none |
| OTHER | UNKNOWN | none |

## Status lifecycle

```text
draft → saved → awaiting_review → approved → submitted → interview → offer
                                                                              ↘ rejected (terminal)
                                                                              ↘ skipped (terminal)
                                                                              ↘ withdrawn (terminal)
```

`needs_review` is a transient status: when an email cannot be confidently resolved to one
application, the best-guess application is flagged `needs_review` (its previous status is
remembered) until the user links or dismisses the email in the review queue, at which point the
previous status is restored or the mapped status is applied.

Terminal statuses cannot transition to active statuses. The email event service only
auto-updates status when the category maps to a known status AND auto-update is enabled AND the
classification confidence is high enough (threshold in
`ApplicationEmailEventService`/`_MIN_CLASSIFICATION_CONFIDENCE`).

## Application matching

Email events are matched to applications by, in order:

1. Explicit `application_id` if provided.
2. Company + vacancy title match (normalized, exact).
3. Text search against company/title (minimum length thresholds).
4. Semantic RAG retrieval over the `vacancies` collection (top candidate must be a clear
   winner).

Unmatched or ambiguous events get flagged `needs_review` and appear in the review queue with
their candidate vacancies.

## Review queue and resolution

`list_review_items()` returns events where `needs_review` is true. The dashboard review panel
shows each pending email (subject, category, confidence, body snippet) with buttons for each
candidate vacancy plus a dismiss action. Resolving:

- **link** — attach the email to the chosen application and apply the category's status (or clear
  `needs_review` when the category has no status).
- **dismiss** — revert the best-guess application to its previous status and unlink the email.

Resolved events are skipped on later re-syncs so they are never re-flagged.

## File-based import

- `EmlImportProvider`: reads `.eml` files from a list of paths. Corrupted files are skipped.
- `MboxImportProvider`: reads `.mbox` files using Python's `mailbox` module.
- `ZipEmlImportProvider`: extracts `.eml` from ZIP archives with path traversal protection
  (rejects `../..` and absolute paths) and decompression bomb protection (max uncompressed size).

All file importers implement `ApplicationEmailProvider` and can be used with
`ApplicationEmailSyncService`.

## Fingerprint deduplication

Each email is fingerprinted by SHA-256 of normalized subject + body. The same message arriving
via IMAP and EML will produce the same fingerprint and be deduplicated by the unique constraint
on `(user_id, message_fingerprint)`.
