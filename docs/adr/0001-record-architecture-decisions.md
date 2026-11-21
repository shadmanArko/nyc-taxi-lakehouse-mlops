# ADR-0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

This project is built in phases and makes many design choices (platform split, tools,
coupling, exposure). Reviewers and future maintainers need to know *why*, not just *what*.

## Decision

Keep lightweight Architecture Decision Records in `docs/adr/`, numbered, one decision each,
in the format: Context, Decision, Consequences. Superseded ADRs stay, marked as superseded.

## Consequences

- Every significant change of direction comes with a short written rationale in the same PR.
- The ADR list doubles as a guided tour of the system's design.
