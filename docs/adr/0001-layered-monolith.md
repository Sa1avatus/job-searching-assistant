# ADR-0001: Layered modular monolith for the initial platform

- Status: accepted
- Date: 2026-07-18
- Owners: project maintainers

## Context

The platform needs durable workflows, deterministic browser execution, validated reasoning outputs,
and future worker distribution. Splitting an empty repository into services would add failure modes
before boundaries are proven.

## Decision

Start as a layered modular monolith. Domain policy has no infrastructure dependencies. Workflow
services depend on repository and browser protocols. SQLAlchemy, Playwright, FastAPI, and future LLM
providers are adapters. PostgreSQL is the supported runtime database; SQLite is permitted only in
isolated automated repository tests.

## Alternatives considered

- Independent microservices: rejected for initial operational complexity.
- A single browser script: rejected because it cannot enforce policy, persistence, or recovery.
- SQLite as the production store: rejected because multi-worker locking is a core requirement.

## Consequences

The first workflow runs as one deployable unit while preserving seams for queues and remote workers.
Database and browser adapters can be replaced without changing truthfulness and submission policy.

## Verification and follow-up

Architecture tests and type checking must prevent domain modules from importing infrastructure.
Revisit service extraction only after measured scaling or isolation requirements appear.
