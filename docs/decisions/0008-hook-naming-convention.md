# 0008 — Hook naming: `entity.{pre,post}_{verb}`

**Status:** Accepted
**Date:** 2026-05-02
**Deciders:** Project owner, fico-expert + plugin-architect + solution-architect council

## Context

OpenSpine plugins extend core via named hooks (`ARCHITECTURE.md` §6.3). Hook
names are referenced as strings in plugin code:

```python
@hook("invoice.pre_post")
def my_handler(ctx, invoice): ...
```

That makes hook names part of the longest-half-life public contract OpenSpine
exposes. Once a plugin ships against a name, renaming costs a two-release
deprecation cycle.

The existing docs were inconsistent on what shape hook names take:

- `docs/README.md:31` declared the canonical form as
  `entity.{pre,post}_{verb}` (e.g., `purchase_order.pre_create`,
  `material.pre_save`, `cost_centre.pre_save`). This is the form used in
  every module's §7 hook table — except FI's.
- `docs/modules/fi-finance.md` §7 used `module_entity.action`
  (`fi_document.pre_post`, `fi_document.pre_reverse`). Ostensibly to
  disambiguate the generic word "document"; the cost was that FI's hook
  surface looked different from every other module's.

This ADR resolves that drift before any plugin ships. There are no plugins
in flight, so there is no compatibility cost — just an editorial one.

## Decision

**The canonical hook-name form is `entity.{pre,post}_{verb}`.** Every module
follows this shape with no module prefix.

Concretely:

- The "document" name was too generic. The FI universal-journal entity is
  renamed to `journal_entry` for hook purposes (each `fin_document_line`
  *is* a journal entry, and the term ties to ADR 0003's universal-journal
  stance).
- FI hooks rename:
  - `fi_document.pre_post` → `journal_entry.pre_post`
  - `fi_document.post_post` → `journal_entry.post_post`
  - `fi_document.pre_reverse` → `journal_entry.pre_reverse`
- `ap_invoice.*` and `ar_invoice.*` already follow `entity.action` and
  stay as-is.
- Every other module's hooks already follow the canonical form; no
  changes needed there.

Plugin authors choose **specific entity names** to avoid collisions. If a
candidate name is too generic across modules (`document`, `record`,
`transaction`), pick a more specific one (`journal_entry`,
`production_order`, `goods_movement`).

The plugin host enforces uniqueness at registration time: two plugins (or
core + a plugin) registering different handlers for the same hook name is
fine; two modules trying to *publish* the same hook name (i.e., both
intending to be the upstream of `widget.pre_save`) is a startup-time
error.

## Alternatives considered

### Option A — `entity.action` everywhere (chosen)

- Pros: matches `docs/README.md:31`; matches every module except FI; shorter
  hook names; cost is one editorial rename in one doc.
- Cons: requires careful entity-name choice to avoid collisions. The
  collision rule (a more specific name when ambiguous) is a discipline,
  not a hard barrier.

### Option B — `module.entity.action` everywhere

- Pros: unambiguous; collisions architecturally impossible; explicit about
  ownership.
- Cons: every module's spec doc would need rewriting; hook names get
  noisier (`mm.purchase_order.pre_create` vs `purchase_order.pre_create`);
  reverses the established direction of the existing spec docs; introduces
  a new convention against the bulk of what's already written.

### Option C — Hybrid: bare `entity.action` when unambiguous, prefixed when not

- Pros: matches the de-facto current state.
- Cons: "is this ambiguous?" is judgement, not lint. Drift-prone — every
  new hook becomes a small naming debate. Plugin authors can't predict
  the form without reading the convention table.
- This is what we *had* and the answer rejects it.

## Consequences

**Positive.**

- One canonical convention; plugin authors learn it once.
- Hook names match the rest of the project's `entity.*` style (event
  streams, REST routes, table prefixes' "what's the entity" question).
- The plugin host can enforce uniqueness — collisions are caught at
  startup, not in the field.

**Negative.**

- Picking a specific entity name when the obvious one is generic requires
  judgement. The convention helps but doesn't eliminate the call.
- Cross-module hooks (e.g., a hook that fires from both MM and PP for
  some shared concept) need a single name owner. Resolved on case-by-case
  basis; no examples in the v0.1 hook catalogue.

**Must remain true for this decision to hold.**

- The plugin host enforces hook-name uniqueness at registration time.
  Without that enforcement, the convention degrades back into the hybrid
  state we're rejecting.
- New module specs cite this ADR when adding hooks.

## References

- `docs/README.md:31` — original convention statement
- `ARCHITECTURE.md` §6.3 — hook catalogue and contract
- `docs/modules/fi-finance.md` §7 — the hook table to be edited
- ADR 0003 — universal-journal terminology that justifies `journal_entry`
- v0.1-foundation.md §6 — listed hook-naming as a v0.1 blocker; this ADR
  closes it
