# 0003 — Universal journal: FI and CO share `fin_document_*`

**Status:** Accepted
**Date:** 2026-05-01
**Deciders:** Project owner, fico-expert + solution-architect council

## Context

Traditional ERPs split Financial Accounting (FI — the legal ledger, GL/AP/AR) and Controlling (CO — management accounting, cost/profit centres, internal orders) into separate ledgers. This historically made sense when the two had different reporting requirements, different period closes, and different aggregation needs. It also generated the entire industry of FI/CO reconciliation: making sure the management view and the legal view agree at the end of the month.

S/4HANA's `ACDOCA` table redesigned this: **one journal table** carrying FI accounts, CO dimensions, and ledger groups as columns on every line. The reconciliation problem disappeared because there is nothing to reconcile.

OpenSpine has the rare opportunity to start from this insight rather than evolve toward it.

## Decision

**FI and CO share a single posting table set.** `fin_document_header` and `fin_document_line` are the universal journal. Every CO dimension that traditionally lived in a separate ledger — cost centre, profit centre, internal order, project, segment, ledger group — is a **column** on `fin_document_line`. CO does not maintain its own postings table.

CO continues to own its **master data** (`co_cost_centre`, `co_profit_centre`, `co_internal_order`, allocation cycles, settlement profiles, derivation rules) under the `co_` prefix. CO **transactions** — actuals, allocations, settlements — manifest as rows on `fin_document_line` with the relevant CO columns populated.

Concretely:

- A primary cost posting (e.g., a vendor invoice with a cost-centre assignment) is a single `fin_document_line` row. The GL account, the cost centre, the profit centre, the internal order, the segment, and the ledger group all sit on that row.
- An allocation cycle execution generates `fin_document_line` rows with secondary cost elements (from `co_cost_element`) and the appropriate sender/receiver cost-object columns.
- An internal-order settlement generates `fin_document_line` rows posting from the order to its receivers per the settlement rule.
- Period close runs over the universal journal once. There is no separate FI close vs CO close.

## Alternatives considered

### Option A — Separate FI and CO ledgers (the legacy approach)

- Pros: matches the practitioner's mental model from older systems; potentially simpler row-level data per ledger.
- Cons:
  - **Reconciliation is permanent overhead.** Every period close becomes "make sure FI and CO agree" — and they don't, naturally, because they aggregate different things at different times. SAP customers have been paying this tax for decades.
  - **Duplication of fact.** A vendor invoice with a cost-centre assignment lives in both ledgers. Two places to update, two places to audit, two places to corrupt.
  - **Stalls multi-dimensional reporting.** Asking "show me revenue by profit centre by ledger by segment YTD" becomes a join across ledgers; the universal-journal model makes it a single scan.
- **Why not chosen:** it solves a problem that isn't ours to inherit. We have no installed base to migrate.

### Option B — Universal journal (S/4HANA-style)

- Pros: no reconciliation; single audit surface; multi-dimensional reporting from a single table; matches modern practitioner expectations from S/4HANA.
- Cons:
  - **Rows get wide.** `fin_document_line` carries many columns, most NULL on any given posting. Index and storage planning matters.
  - **Some practitioners trained on classical ECC find it unfamiliar at first.** This is a documentation problem, not a design problem.
- **Why chosen:** every cost is bounded and addressable; every benefit is structural.

### Option C — Universal journal but with CO posting table as a derived view

- Compromise: physical universal journal, but expose a CO-shaped logical view (`co_actual_postings`) for CO-trained practitioners who want it.
- Pros: practitioner ergonomics for CO power users.
- Cons: views drift from physical structure; encouraging code that joins to a "CO postings" abstraction reintroduces the reconciliation mental model.
- **Why not chosen now, but kept as an option:** if pilot CO consultants find direct querying of `fin_document_line` hostile, a read-only CO view is a small extension. Not part of v0.1.

## Practical implications

1. **Authorisation.** A user with FI permissions can post entries that *also* affect CO (because the cost centre column is on the same row). Authorisation objects live on the action (`fi.document.post`), with CO scope qualifiers (`controlling_area`, `cost_centre_range`) on the same auth check. This is captured in `permissions.md`.

2. **Hooks.** There is no separate "CO posting" hook; `journal_entry.pre_post` and its siblings (per ADR 0008's naming convention) cover everything. CO-specific behaviours (allocation execution, settlement) have their own hooks (`allocation.pre_run`, `settlement.pre_run`) that *trigger* universal-journal writes via the FI posting service, never bypass it.

3. **Module boundaries.** CO calls FI's posting service; FI does not call CO. The CO master tables are read by FI when validating a posting's cost-object assignments, but FI does not write CO master.

4. **Hook naming consistency.** Resolved by ADR 0008 — FI hooks use `entity.action` like every other module, with `journal_entry` as the universal-journal entity name. This ADR's references to `journal_entry.pre_post` etc. follow that convention.

5. **Multi-currency, parallel ledgers.** The universal journal carries local / document / group / hard currency columns and a `ledger_group` column per line. Parallel ledgers (e.g., `0L` leading IFRS, `2L` local GAAP) are rows in a different ledger group, not a different table. Default deployment is one ledger; additional ledgers are opt-in at Company Code level (`fi-finance.md` open Q1).

## Consequences

**Positive.**

- Zero FI/CO reconciliation overhead. Period close is one operation, not two.
- One audit surface: one immutable journal that auditors and management both query.
- Multi-dimensional reporting (by GL, cost centre, profit centre, segment, ledger group) is a single-table aggregation.
- Less code, fewer tables, simpler service interfaces between FI and CO.
- Mirrors S/4HANA's modern stance, which lowers the migration cost for prospective customers coming from there.

**Negative.**

- `fin_document_line` is a wide table with many NULL-tolerant columns. Index strategy matters; documented in `docs/architecture/data-model.md` (to be written in v0.1).
- Practitioners trained on classical FI/CO will need orientation. Documentation effort, not a design issue.
- Migrations from classical SAP ECC require a transformation step (split + re-merge into universal journal). Migration tooling lands at v1.0 per the roadmap.

**Must remain true for this decision to hold.**

- The CO dimensions remain a small, fixed set (cost centre, profit centre, internal order, segment, project, ledger group). If the dimension list grows unboundedly (e.g., per-customer arbitrary analytical dimensions), the wide-table approach becomes painful and a column-set / EAV design enters the conversation. This isn't expected for Phase 1.

## References

- `ARCHITECTURE.md` §5 — module boundaries and the universal-journal explanation.
- `docs/modules/README.md` §"Cross-module principles" — universal-journal philosophy.
- `docs/modules/fi-finance.md` §4 — `fin_document_*` schema description.
- `docs/modules/co-controlling.md` §4 — CO master data (`co_*`) ownership.
- SAP `ACDOCA` documentation — prior-art reference. We adopt the structural pattern, not the proprietary implementation.
