# Architectural Decision Records (ADRs)

This directory collects ADRs — short, numbered documents that record significant architectural decisions, their context, the options considered, and the reasoning behind the chosen path.

## Format

Each ADR is a single Markdown file named `NNNN-short-title.md` where `NNNN` is a zero-padded sequence number. ADRs are **immutable** once merged — they capture the decision at a point in time. If a decision is reversed, a new ADR supersedes the old one (linking back).

## Template

```markdown
# NNNN — Short title

**Status:** Proposed | Accepted | Superseded by NNNN
**Date:** YYYY-MM-DD
**Deciders:** Beyhan Meyralı, (others as applicable)

## Context

What is the problem? What forces are at play (technical, business, social)?

## Decision

The chosen path, stated clearly and briefly.

## Alternatives considered

- **Option A** — description, pros, cons, why not chosen
- **Option B** — description, pros, cons, why not chosen

## Consequences

Positive and negative implications. What becomes easier? What becomes harder? What must be true for this decision to remain valid?

## References

Links to related discussions, issues, prior art.
```

## Planned initial ADRs

Populated as decisions are formalised. High-priority candidates:

- `0001-postgres-as-source-of-truth.md` — Why PostgreSQL is authoritative and Qdrant is a derivative
- `0002-qdrant-over-pgvector.md` — Vector store choice
- `0003-universal-journal.md` — Single `fin_document_line` for FI and CO
- `0004-agpl-license.md` — Why AGPL-3.0, not GPL / MIT / Apache
- `0005-multi-tenant-by-default.md` — RLS + service-layer filtering
- `0006-agents-as-first-class-principals.md` — No privileged agent backdoor
- `0007-two-tier-rbac.md` — Composite + single roles, SAP-style
- `0008-hook-naming-convention.md` — `entity.{pre,post}_{verb}`
- `0009-no-cross-module-joins.md` — Service-only cross-module reads
- `0010-python-fastapi-stack.md` — Backend stack selection

When an ADR is finalised, update the list above with its actual filename and status.
