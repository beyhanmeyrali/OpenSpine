# NEEDS-INPUT — questions queued for owner

Decisions that came up during the overnight session and the morning
council pass. Once decided, the resolution is captured here, in an ADR,
or in the relevant doc, and the entry is removed.

---

## Resolved on 2026-05-02 (this session)

1. ~~**Hook-naming canonical form.**~~ → ADR 0008 (entity.action; FI's
   `fi_document.*` → `journal_entry.*`).
2. ~~**Audit-log topology.**~~ → New "Audit topology" section in
   `docs/identity/README.md`. Three tables stay; `trace_id` joins.
3. ~~**Database-per-tenant deployment variant.**~~ → ADR 0005:
   rejected forever. Logical isolation only. Default deployment is
   single-tenant per installation.
4. ~~**Qdrant collection topology threshold.**~~ → ADR 0002 amended:
   collection-per-tenant forever; the previously-floated 500-tenant
   revisit clause is dropped. Captured in ADR 0005.
5. ~~**FX-rate `mid` reference.**~~ → `permissions.md` updated: auth
   amount conversions use rate type `M` (average), never `B` or `G`,
   to avoid directional bias.

---

## Still open

### A. AGPL plugin distribution (deferred)

**Status:** owner chose to skip. Not blocking v0.1.

**Question.** AGPL-3.0's network-interaction clause in the context of
plugins running in-process on a network-accessible OpenSpine deployment.
Three plausible legal stances:

- Strict — plugins are part of the combined work; AGPL applies.
- Lenient — plugins are separate works.
- Pragmatic — bundled distribution = combined work; runtime-loaded
  entry-point plugins = separate works.

**Owner direction:** address only when an actual adopter asks.

### B. Identity-core schema review process (§4.2)

**Status:** owner chose council-drafts-first. Defer until a future
session can convene the council.

**Question.** When ready to land §4.2 (identity tables, RLS,
principal-context middleware), spawn `identity-expert` +
`ai-agent-architect` + `solution-architect` to draft the strategic
decisions (column types, RLS policy shape, audit-trigger pattern,
agent-token shape). Owner reviews the joint design. Implementation
follows.

This is the highest-stakes remaining unit of v0.1 code. Schema choices
are hard to undo.

### C. Cross-tenant consolidation reporting (post-v1.0)

**Status:** genuinely open per `docs/identity/tenancy.md`. Not v0.1.

Group-level reporting across tenants (a holding company with per-entity
tenants). Probably a separate reporting tenant pulling from source
tenants via authorised read-only APIs.

### D. Tenant move (post-v1.0)

**Status:** genuinely open per `docs/identity/tenancy.md`. Not v0.1.

Exporting and re-importing a tenant cleanly. Needs a structured export
format.

---

## What I did NOT touch (and why)

For transparency:

- **No ADR 0004.** Owner directed skip on AGPL legal nuance.
- **No identity migrations.** Owner directed council-drafts-first for §4.2.
- **No force-push, no history rewrite, no branch deletion.**
- **No new dependencies in `pyproject.toml`** beyond what was declared
  in v0.1 §1.7's observability list.
- **No edits to LICENSE or top-level README narrative.**
- **No changes to module spec docs** beyond:
  - `md-master-data.md` — fixed dangling `data-model.md` reference.
  - `fi-finance.md` — applied the ADR 0008 hook renames.
  - `mm-materials.md` — fixed cross-reference to renamed FI hook.
  - `tenancy.md` — replaced Q1 with the deployment-model + non-negotiable.
  - `permissions.md` — fixed FX rate reference per item 5 above.
  - `identity/README.md` — added Audit topology section.
