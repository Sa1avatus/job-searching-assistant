# ADR 0003: Canonical autofill values and declarative browser workflows

- Status: accepted
- Date: 2026-07-30
- Owners: project maintainers

## Context

Common user data, such as name and contact information, currently lacks a
reusable canonical source. Each site-specific integration duplicates and
stores personal values independently.

Fixed site automation scripts cannot safely scale to arbitrary job sites.
Site markup changes frequently, and storing literal personal values in
scripts creates security and maintenance burdens. A declarative, versioned
workflow language is needed to manage browser interactions without custom
code generation.

## Decision

1. Common autofill values are user-scoped canonical records. Sites store
   mappings to canonical keys rather than copied personal values.

2. The effective value for a field is resolved in this precedence order:
   - Application override
   - Site-field override
   - Site-value override
   - Selected-resume value
   - Global user value
   - Generated value
   - Manual input

3. Browser automation uses Playwright with a closed, typed, versioned
   declarative workflow language. Generated Python or arbitrary JavaScript
   steps are forbidden.

4. Workflows are drafts until a successful dry run. Activation creates or
   promotes an explicit version with rollback history.

## Safety boundaries

- Every site definition has an explicit HTTPS host allowlist.
- Cross-host navigation fails closed unless the new host is separately
  approved.
- Passwords and one-time codes are never stored as autofill values.
- CAPTCHA solving is never automated.
- Arbitrary JavaScript execution is forbidden.
- Direct LLM control of browser actions is not allowed.
- Sensitive values are never sent to an LLM.
- Submit is a distinct privileged step requiring human confirmation by
  default.
- Automated tests never submit a real application.

## Consequences

- Centralized updates to a canonical value propagate to all mapped sites.
- Sites, fields, and applications can override canonical values when needed.
- Migrating existing HeadHunter and LinkedIn paths requires compatibility
  shims and controlled rollout.
- Sensitive autofill storage must use encrypted persistence.
- Locator maintenance becomes versioned and requires periodic review.
- Specialized adapter escape hatches remain available for genuinely
  non-generic site behavior.

## Verification and follow-up

- Unit tests verify precedence resolution and safety policy enforcement.
- Migration validation ensures existing data is correctly represented.
- Controlled fixture browser tests validate core workflow execution.
- A dry-run proof confirms submit steps cannot execute automatically.
- Restart persistence checks verify workflow versions survive process
  restarts.
- Compatibility tests validate continued operation for HeadHunter and
  LinkedIn.
