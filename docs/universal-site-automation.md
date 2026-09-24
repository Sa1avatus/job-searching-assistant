# Universal site automation

Read this document when changing canonical autofill values, arbitrary site definitions, discovered
fields, mappings, overrides, or declarative browser workflows.

## Implemented foundation

- User-scoped canonical autofill values with controlled standard and `custom.*` keys.
- Encryption and review rules for sensitive values.
- Arbitrary HTTPS site definitions with validated hosts and soft archive.
- Generalized browser-session status for configured sites.
- Discovered site fields, canonical mappings, encrypted site/field overrides, and deterministic
  effective-value resolution.
- Multiple semantic locator candidates from form discovery.
- A closed `WorkflowStepType` enum, a typed schema for every step
  (`app/domain/workflow_*.py`), and a per-step Playwright executor for each one
  (`app/browser/workflow_*_executor.py`, dispatched by `app/workflows/`). `WorkflowDefinitionRow`/
  `WorkflowStepRow` persistence exists (migration `0024`).
- Search recipes for user-defined sites (migration `0048`): see the next section. Its recorded
  `reach_steps` (below) are the first real caller of the step executor above.

The current Alembic head for the autofill foundation is `0023`, which covers site fields, mappings,
and overrides. `workflow_definitions`/`workflow_steps` have a migration and a working per-step
executor, but nothing outside `app/workflows/workflow_runner.py` itself calls it yet: there is no
CRUD service, no API, no recorder, and no activation/rollback/dry-run lifecycle for a full
job-application-form workflow (fill + select + check + upload + the privileged submit step with
human confirmation). Do not document arbitrary-site application-form execution as operational yet.

## Search recipes for user-defined sites

A site definition can be searched through a `SearchRecipe` (`app/domain/search_recipe.py`): an HTTPS
URL template containing `{query}` (and optionally `{location}`) plus plain CSS selectors for the
result card, the link inside it, the title and the company. It is data, never code.

- **Learning.** `POST .../site-definitions/{id}/search-recipe/learn` takes the URL of a results page
  the person reached themselves and the query they typed. The worker loads that page (with the saved
  session, if any), replaces the query in the URL with `{query}`, finds the repeated cards from the
  shared URL shape of the vacancy links (`app/browser/recipe_learning.py`, stdlib HTML parsing) and
  proves the result by running the derived recipe. The recipe can also be written by hand
  (`PUT .../search-recipe/draft`).
- **Lifecycle.** Drafts never run in discovery. A test run (`POST .../{version}/test`) that returns
  results marks the version verified; only verified versions can be activated. Activating archives
  the previous active version, which can be activated again to roll back.
- **Reading pages.** `app/browser/custom_site.py` uses Playwright locators and `text_content` only.
  Selectors are validated as plain CSS (engine prefixes, chaining and markup are rejected) and are
  always evaluated with the `css=` engine. Every start URL, redirect target and result link must be
  on an exact host from the site's allowlist, otherwise the request is refused.
- **Vacancy extraction.** schema.org `JobPosting` JSON-LD when present, otherwise the page `h1`, the
  `og:site_name` meta tag and the main text.
- **Discovery.** An active recipe makes the site a `custom:<site_key>` source in the discovery
  stream. Vacancies are stored with adapter `custom` and are review-only: no automatic submission.
- **Recording a scenario instead of a URL template.** When search needs a POST form or an
  in-page click, `recipe.reach_steps` (`app/domain/search_recipe.py`) holds a short recorded
  sequence of `navigate`/`fill`/`click` steps - a restricted subset of ADR 0003's closed
  `WorkflowStep` schema; no `select`/`check`/`upload`/`submit`/`human_review` allowed here. It is
  validated the same way as a URL template: every `navigate` host must be on the site's allowlist,
  every `fill` step's `value_key` must be `query` or `location`, and at least one `query` fill is
  required. At search time (`app/browser/custom_site.py::search_custom_site`) `reach_steps` take
  priority over `url_template` when both are present; they are replayed with the existing
  typed-step executor (`app/workflows/workflow_runner.py`) and the resulting page is checked
  against the host allowlist before cards are read the normal way.
  - **Recording UI.** "Записать сценарий поиска" opens the same visible, noVNC-streamed session
    already used for site login (`app/services/browser_authorization.py`'s pattern, mirrored by
    `app/services/search_recipe_recording.py`). A fixed script
    (`app/browser/assets/search_recorder.js`) observes clicks and completed field edits and
    reports them through an exposed binding - it never acts on the page, and password fields are
    never observed. `POST .../search-recipe/record/{start,stop,cancel}` proxy to the browser
    worker. Stopping returns each action's locator candidates (`app/browser/search_reach_recording.py`,
    same priority as form discovery: id/name/test-id/label/placeholder/role) and, for `fill`
    actions only, the typed text - safe here because it is the person's own search text, not
    personal data. The person tags each field as query/location/ignore and can drop clicks before
    saving; nothing is persisted until `PUT .../search-recipe/draft`.

Known limits: an infinite scroll is not covered by either path, and the query must appear in the
results URL for automatic learning (otherwise write the recipe by hand or record it).

## Value precedence

Effective field values resolve in this order:

1. application override;
2. site-field override;
3. site-wide override;
4. selected-resume value;
5. global user value;
6. generated value;
7. manual input.

Password and one-time-code values are not valid autofill sources. Sensitive values require explicit
permission and must not be sent to an LLM.

## Workflow safety contract

ADR 0003 defines the intended closed, typed, versioned workflow language. Any continuation must
preserve these invariants:

- exact HTTPS host allowlists and fail-closed cross-host navigation;
- no arbitrary Python or JavaScript steps;
- semantic locator references instead of recorded literal personal data;
- bounded timeouts and deterministic outcome checks;
- drafts remain non-runnable until validated by a controlled dry run;
- `submit` is a separate privileged step requiring human confirmation by default;
- version activation is reversible and never silently replaces a working version;
- automated tests use controlled local pages and prove that submit remains blocked.

## Remaining implementation order

For a full job-application-form workflow (`workflow_definitions`/`workflow_steps`, all ten step
types including `submit`):

1. ~~Add the migration and typed persistence for workflow definitions and individual steps~~ (done:
   migration `0024`).
2. ~~Add one validated step schema and deterministic executor at a time~~ (done: every
   `WorkflowStepType` has a schema and an executor).
3. Add a CRUD service and API, outcome rules, dry-run evidence, activation, and rollback.
4. Add recorder and editor behavior without storing literal secrets. The search-recipe recorder
   (above) is the model to follow: observe-only script, candidates over recorded values, tag
   before persisting.
5. Introduce explicit submit approval and compatibility adapters only after fixture and restart tests
   pass.

Track active implementation work in issues or Worker task files, not in this reference document.
