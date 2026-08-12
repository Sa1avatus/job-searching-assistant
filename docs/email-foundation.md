# Email foundation

Read this document when changing email classification, event processing, application status
updates, or file-based email import.

## Components

- `app/domain/application_email.py` defines `ApplicationEmailOutcome` (REJECTED, NEXT_STAGE,
  OFFER, UNKNOWN), `EmailCategory` (10 categories from APPLICATION_RECEIVED to OTHER), and
  `outcome_for_category()` mapping.
- `app/domain/application_status.py` defines `ApplicationStatus` with `TERMINAL_STATUSES`
  (rejected, skipped, withdrawn) and `ACTIVE_STATUSES` (all others).
- `app/services/application_email_classifier.py` provides `classify_application_email()` (legacy
  outcome) and `classify_email_category()` (new category taxonomy). Both use regex patterns for
  EN and RU.
- `app/services/application_email_events.py` provides `ApplicationEmailEventService` with
  `ingest()` (classify, deduplicate by fingerprint, match to application, optionally update
  status) and `list_review_items()` (returns unknown-outcome or unmatched events for human review).
- `app/services/application_email_sync.py` provides `ApplicationEmailSyncService` for batch
  processing from any `ApplicationEmailProvider`.
- `app/services/imap_email_provider.py` implements `ApplicationEmailProvider` for IMAP.
- `app/services/email_file_import.py` provides `EmlImportProvider`, `MboxImportProvider`, and
  `ZipEmlImportProvider` for batch file-based import.
- `app/storage/tables.py` defines `ApplicationEmailEventRow` with message fingerprint for
  idempotent deduplication.

## Email categories

| Category | Outcome | Status update |
|---|---|---|
| APPLICATION_RECEIVED | UNKNOWN | none |
| RECRUITER_CONTACT | UNKNOWN | none |
| QUESTION | UNKNOWN | none |
| TEST_ASSIGNMENT | NEXT_STAGE | none (manual) |
| INTERVIEW_INVITATION | NEXT_STAGE | → interview |
| INTERVIEW_RESCHEDULE | NEXT_STAGE | → interview |
| OFFER | OFFER | → offer |
| REJECTION | REJECTED | → rejected |
| FOLLOW_UP | UNKNOWN | none |
| OTHER | UNKNOWN | none |

## Status lifecycle

```text
draft → saved → awaiting_review → approved → submitted → interview → offer
                                                                              ↘ rejected (terminal)
                                                                              ↘ skipped (terminal)
                                                                              ↘ withdrawn (terminal)
```

Terminal statuses cannot transition to active statuses. The email event service only auto-updates
status when outcome maps to a known status AND auto-update is enabled AND the confidence is high
enough (controlled by `ApplicationEmailEventService.auto_update_enabled`).

## Application matching

Email events are matched to applications by:
1. Explicit `application_id` if provided.
2. Company + vacancy title match (normalized, exact).
3. Text search against company/title (minimum length thresholds).

Unmatched events get `application_id = NULL` and appear in the review queue.

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

## Review queue

`list_review_items()` returns events where:
- outcome is `unknown` (ambiguous classification), OR
- `application_id` is NULL (unmatched to any application).

These require human review before status can be auto-applied.
