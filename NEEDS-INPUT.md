# NEEDS-INPUT — questions queued for owner

Decisions that came up during the overnight session but were deliberately
not made unattended. Listed in rough priority order — earlier items unblock
more downstream work.

Once a decision is made, please:

1. Capture the answer here (or, for durable architectural calls, draft an
   ADR in `docs/decisions/`).
2. Update `docs/roadmap/v0.1-foundation.md` if scope or sequencing changes.
3. Remove the resolved entry from this file.

---

## 1. Hook-naming canonical form

**Status:** v0.1 §4.6 BLOCKER. The plugin host can't harden until this is
decided.

**Question.** The convention in `docs/README.md:31` is `entity.{pre,post}_{verb}`
(e.g., `purchase_order.pre_create`, `cost_centre.pre_save`). FI's spec
(`docs/modules/fi-finance.md` §7) uses a `module_entity.action` shape
instead (`fi_document.pre_post`, `ap_invoice.pre_post`). Which is canonical?

**Why it matters.** Plugins reference hook names as strings. Once a plugin
ships against `fi_document.pre_post`, renaming requires a deprecation cycle.
Pick now, before plugins exist.

**Options I see (no recommendation — this is your call):**

A. **`entity.action` everywhere.** Rename FI hooks to `document.pre_post`,
   `ap_invoice.pre_post`, etc. Pro: simpler, matches `docs/README.md` and
   most modules already. Con: `document` is generic; the FI doc may have
   used the prefix to disambiguate from other modules' "documents".

B. **`module.entity.action` everywhere.** Rename MM/PP/CO hooks to
   `mm.purchase_order.pre_create`, `pp.bom.pre_save`, etc. Pro:
   unambiguous. Con: noisier; most modules already use the shorter form.

C. **Hybrid with rule.** Bare `entity.action` when the entity is unambiguous
   project-wide; `module_entity.action` only when the entity name would
   collide. Pro: the de-facto current state. Con: drift-prone; "is this
   ambiguous?" is judgement, not lint.

**Recommended path to resolve.** Open a council session with
`fico-expert`, `plugin-architect`, and `solution-architect`. They've each
got a stake. SA synthesizes; result becomes an ADR (e.g.,
`0008-hook-naming-convention.md`).

---

## 2. AGPL plugin distribution implications

**Status:** Held back from unattended drafting (legal nuance).

**Question.** The README and ARCHITECTURE.md commit to AGPL-3.0 forever
*and* to private plugins distributed inside a company. AGPL-3.0's
network-interaction clause means a plugin running against an OpenSpine SaaS
that serves third parties may pull the plugin under the source-disclosure
obligation.

We need an explicit statement covering:

- Which plugin distribution modes (private / PyPI / marketplace) trigger
  AGPL source-disclosure.
- Treatment of internal-only plugins on multi-tenant SaaS that serves
  third parties.
- Contributor licensing — does OpenSpine require a CLA / DCO + CLA, or is
  AGPL alone sufficient (the current CONTRIBUTING.md says DCO only).

**Why it matters.** Adopters considering OpenSpine for SaaS will block on
this. Until the boundary is documented, the answer is implicitly "consult
your lawyer", which is a lousy answer.

**Recommended path.** Get input from someone with AGPL experience (Bradley
Kuhn / Conservancy / similar), draft `0004-agpl-license.md`. I have not
attempted to draft it because the legal bits exceed unattended judgement.

---

## 3. Database-per-tenant vs shared+RLS (deployment variant)

**Status:** Not blocking v0.1. The v0.1 plan default is shared+RLS, which
is enough to proceed.

**Question.** `docs/identity/tenancy.md` Q1 asks whether OpenSpine should
support a database-per-tenant deployment variant for regulated workloads.

**Why it might matter.** Some regulated industries (healthcare, defence)
will require physical isolation per tenant. RLS is logical isolation; some
auditors don't accept it.

**Recommended path.** Defer. Ship shared+RLS in v0.1 as planned. Revisit
when a regulated-workload customer is in the pipeline. ADR if it lands.

---

## 4. Qdrant collection topology threshold

**Status:** Not blocking v0.1. Per ADR 0002, default is collection-per-tenant.

**Question.** At what tenant cardinality do we revisit the
collection-per-tenant choice in favour of shared collection with
tenant-keyed payload filtering? The ADR floats "~500 tenants" as a
gut-feel revisit point.

**Recommended path.** Operational signal — once a real deployment crosses
~100 tenants, monitor Qdrant cluster overhead and decide. No action needed
until then.

---

## 5. FX-rate `mid` reference

**Status:** Tiny doc inconsistency. Easy to fix; just need a one-word
answer.

**Question.** `docs/identity/permissions.md:73` says amount qualifiers are
converted "using the current mid-rate". `docs/modules/md-master-data.md:61`
defines three rate types: `M` (average), `B` (bank-selling), `G` (bank-buying).
There's no `mid`.

**Options:**

A. Change `permissions.md` to say "current `M` (average) rate". (Most
   likely the intent; "mid" is colloquial for "average".)

B. Add a `mid` rate type to `md_exchange_rate_type` because mid is a
   distinct convention in some markets.

**Recommended.** Option A — cheaper, matches the existing catalogue. Will
apply if you confirm.

---

## 6. Audit-log topology — write the missing section

**Status:** Three audit-related tables exist (`id_audit_event`,
`id_auth_decision_log`, `id_agent_decision_trace`) but the relationship
between them is implicit.

**Question.** Is the intended split:

- `id_audit_event` — every authentication, every business-data write
  (the "what happened" log)
- `id_auth_decision_log` — every authorisation decision the auth-object
  engine made (the "what was allowed/denied" log)
- `id_agent_decision_trace` — every agent's reasoning trace (the "why an
  agent did what it did" log)

…with `trace_id` as the join key across all three?

**Why it matters.** Compliance reviewers will ask "show me everything that
happened with this user, this session, this transaction". The join model
needs to be explicit.

**Recommended path.** Confirm the split. I'll write a short
"audit topology" section in `docs/identity/README.md` codifying it.

---

## 7. Identity-core schema review (when ready to build §4.2)

**Status:** §4.2 is technically unblocked by the v0.1 plan, but I've held
off on schema design to give you a chance to review.

**Question.** When you're ready to land §4.2, would you like me to:

A. Convene the council (`identity-expert`, `ai-agent-architect` for
   agent-token shape, `solution-architect`) to draft the migration before
   I write it; you review their joint recommendation; I implement?

B. Draft the migration myself based on the existing identity docs; you
   review the diff; iterate?

C. Pair on it interactively when you're awake?

I lean (A) for the strategic decisions (column types, RLS policy shape,
audit-trigger pattern) and (B) for the mechanical migration once those
are pinned. (C) if you'd rather just sit together.

---

## What I did NOT touch (and why)

For transparency: things that came up but I declined to do unattended.

- **Hook-name renames.** Would touch the FI spec doc and any code referencing
  the names. Waiting on §1 above.
- **ADR 0004 AGPL plugin distribution.** Legal nuance.
- **Identity migrations.** §7 above.
- **Force-push, history rewrite, branch deletion.** Never under any conditions.
- **Adding new dependencies to `pyproject.toml`** beyond what was already
  declared in v0.1 §1.7's observability list.
- **Editing the LICENSE file or top-level README's narrative content.**
- **Modifying the existing module spec docs** beyond the dangling-link fix
  in `md-master-data.md` and the doc-review pass.
