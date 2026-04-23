# OpenSpine — Documentation

Entry point for all non-code documentation. For the project pitch and vision, see the repository [README](../README.md). For the high-level system design, see [ARCHITECTURE](../ARCHITECTURE.md).

## Reading order

If you are new to the project, read in this order:

1. [README](../README.md) — what OpenSpine is and why it exists
2. [ARCHITECTURE](../ARCHITECTURE.md) — how it is put together
3. [modules/README](./modules/README.md) — what the functional modules cover and the order we build them in
4. [identity/README](./identity/README.md) — how users, agents, roles, and permissions work
5. [roadmap/README](./roadmap/README.md) — the release milestones
6. [decisions/README](./decisions/README.md) — architectural decision records (ADRs)

## Structure

```
docs/
├── README.md              ← you are here
├── architecture/          ← deep-dives referenced from /ARCHITECTURE.md
├── modules/               ← functional module scope (FI, CO, MM, PP, MD)
├── identity/              ← users, tenants, roles, permissions, auth
├── roadmap/               ← release milestones v0.1 → v1.0
└── decisions/             ← ADRs — one file per decision
```

## Conventions

- **Module table prefixes.** `md_` master data, `fin_` finance (FI posting tables + CO assignments on the universal journal), `co_` CO-owned master (cost centers, profit centers, internal orders), `mm_` materials management, `pp_` production planning, `id_` identity and access.
- **Hook names** follow `entity.{pre,post}_{verb}` — e.g. `invoice.pre_post`, `production_order.post_confirm`. Verbs are stable; adding new hooks is non-breaking, renaming goes through a two-release deprecation cycle.
- **Every module doc** follows the same 9-section template (purpose, Phase 1 scope, deferred scope, core entities, key transactions, integrations, hooks, AI agent affordances, open questions). Consistency makes the docs comparable at a glance.
- **Table and entity names** are snake_case. Column names are snake_case. Class names in Python are PascalCase. Class names in TypeScript follow the React/TS conventions.
- **Authoritative source of truth** is PostgreSQL. Qdrant is a derivative. No doc should imply otherwise.

## Status

All documents here describe **intent**. Code does not exist yet. When code lands, we update docs to reflect the built reality and mark deviations explicitly.
