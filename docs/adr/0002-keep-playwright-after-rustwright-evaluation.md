# ADR 0002: Keep Playwright after Rustwright alpha evaluation

- Status: accepted
- Date: 2026-07-18
- Candidate: `Skyvern-AI/rustwright` commit
  `0b45995621a6464a5ca7a95839b6c4b4470c11b1`, PyPI wheel `0.1.1`

## Context

Rustwright claims lower driver overhead through an in-process Rust CDP engine. The project needs
async Chromium contexts, accessible locators, file uploads, screenshots, and storage-state recovery.
A replacement is acceptable only when the candidate passes security review, behavior compatibility,
and a benchmark representative of this repository.

## Evidence

- Source and wheel review found no explicit telemetry, credential collection, unrelated persistence,
  or suspicious install hook. Microsoft Defender reported no new detection for the cloned source and
  downloaded wheel.
- The native `_rustwright.pyd` wheel component is not Authenticode-signed. Absence of a scanner
  detection does not prove that native code is harmless.
- Rustwright is explicitly alpha and Chromium-only. Its Python async API delegates sync operations
  through threads and does not claim complete behavioral parity.
- Installing the wheel registers a pytest plugin with an autouse fixture that recursively removes
  the configured `test-results` directory at session start. This is documented test-artifact behavior,
  but it is an additional destructive side effect for this repository.
- The bundled browser downloader follows a current Chrome-for-Testing manifest and extracts the ZIP
  without a repository-pinned archive digest. This project would have to avoid that path and provide
  a separately verified Chromium binary.
- The reviewed `0.1.1` wheel's Python tree differs from current `main`; the repository version was not
  advanced, so a commit pin alone does not identify the published native wheel contents.
- The candidate successfully executed the controlled application workflow, including accessible
  locators, upload, screenshot, and storage-state export.
- Two alternating 10-iteration local runs against the same Chromium and fixture measured:

  | Engine | Launch | Mean iteration | Median iteration |
  | --- | ---: | ---: | ---: |
  | Playwright run 1 | 878 ms | 278 ms | 244 ms |
  | Playwright run 2 | 902 ms | 222 ms | 199 ms |
  | Rustwright run 1 | 1,059 ms | 550 ms | 509 ms |
  | Rustwright run 2 | 1,034 ms | 473 ms | 426 ms |

## Decision

Keep official Playwright. Rustwright was approximately twice as slow on the repository's actual
async review workload, so the required performance gate failed independently of the alpha/supply-
chain concerns. Do not add Rustwright as a dependency or fallback engine.

Re-evaluate only after a stable release provides reproducible signed artifacts, safer browser
installation integrity, and a repository-local benchmark advantage.
