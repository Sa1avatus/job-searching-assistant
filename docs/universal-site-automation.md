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

The current Alembic head is `0023`, which covers site fields, mappings, and overrides. Workflow
definition persistence has no migration, and step schemas, execution, activation, rollback,
recording, visual editing, and dry-run evidence are not complete. Do not document arbitrary-site
execution as operational yet.

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
