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
- A closed `WorkflowStepType` enum and an ORM `WorkflowDefinitionRow` foundation.

- Search recipes for user-defined sites (migration `0048`): see the next section.

The current Alembic head for the autofill foundation is `0023`, which covers site fields, mappings, and overrides. Workflow
definition persistence has no migration, and step schemas, execution, activation, rollback,
recording, visual editing, and dry-run evidence are not complete. Do not document arbitrary-site
execution as operational yet.

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

Known limits: results that need a POST, an infinite scroll or an in-page click are not covered, and
the query must appear in the results URL for automatic learning (otherwise write the recipe by hand).

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

1. Add the migration and typed persistence for workflow definitions and individual steps.
2. Add one validated step schema and deterministic executor at a time.
3. Add outcome rules, dry-run evidence, activation, and rollback.
4. Add recorder and editor behavior without storing literal secrets.
5. Introduce explicit submit approval and compatibility adapters only after fixture and restart tests
   pass.

Track active implementation work in issues or Worker task files, not in this reference document.
