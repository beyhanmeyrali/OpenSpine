# 0005 — Tenant isolation: shared schema + RLS only

**Status:** Accepted
**Date:** 2026-05-02
**Deciders:** Project owner, identity-expert + solution-architect council

## Context

OpenSpine is multi-tenant by design. Two related questions had been left
open in the docs:

1. `docs/identity/tenancy.md` Q1 — should OpenSpine support a
   **database-per-tenant** deployment variant for regulated workloads
   (some healthcare, defence, and financial regulators don't accept
   logical isolation)?
2. `docs/decisions/0002-qdrant-over-pgvector.md` — at what tenant
   cardinality do we revisit the **collection-per-tenant** topology
   in favour of shared collection + payload filter?

The two questions share an axis: how strong an isolation guarantee does
OpenSpine offer, and how scalable is that guarantee?

A third unstated assumption was implicit throughout the docs: who is the
typical OpenSpine deployer? The answer turned out to clarify both
questions at once.

## Decision

OpenSpine commits to **logical isolation only**:

1. **Tenancy in PostgreSQL is shared schema with RLS.** Database-per-tenant
   is rejected — *forever*. No deployment variant supports a separate
   Postgres database per tenant.

2. **Tenancy in Qdrant is collection-per-tenant.** This stays the
   architecture forever. The previously-floated "revisit at ~500
   tenants" clause in ADR 0002 is dropped.

3. **The default deployment model is single-tenant per installation.**
   Each adopting company runs their own OpenSpine instance with their
   own Postgres, Qdrant, Redis, and embedding worker. The "multi-tenant"
   architecture exists so that a *third party who chooses to host
   OpenSpine as cloud SaaS* can serve multiple customer tenants from
   one installation. Self-hosting deployments — the typical case — run
   one tenant.

4. **Multi-tenant SaaS hosting is opt-in, by an external hoster, on their
   own operational terms.** OpenSpine does not run a SaaS service. If a
   hoster chooses to operate OpenSpine multi-tenant at scale (hundreds
   or thousands of tenants), the operational scaling story (Qdrant
   cluster sizing, Postgres pooling, etc.) is their concern. The
   architecture won't be revisited to accommodate that scale.

## Why these together

The four points above are inseparable:

- If the typical deployment is single-tenant per install, "scale to many
  tenants" is a SaaS-hoster concern, not a core-architecture concern.
- That makes collection-per-tenant cheap (typically 1 collection per
  installation) and removes the pressure to weaken isolation under load.
- Database-per-tenant adds operational complexity (N databases, N
  migration runs, N pooling concerns) for no value to the typical
  deployment, and only marginal value to a SaaS hoster who's already
  signed up for operational complexity.
- Logical isolation (RLS + service filter + per-tenant Qdrant collection)
  is the same in single-tenant and SaaS deployments. One mental model.

## Alternatives considered

### Option A — Support database-per-tenant as a deployment variant

- Pros: stronger isolation; some regulators are happy with it where they
  reject RLS; per-tenant backup/restore is trivial.
- Cons: every migration runs N times; service-layer connection routing
  becomes a real subsystem; pool fragmentation; per-tenant capacity
  planning; no single observability surface; rejected outright by the
  project owner because the deployment model doesn't justify it.
- **Why not chosen:** the typical deployment is single-tenant per install.
  Adding a parallel deployment variant for a market segment we're not
  targeting is gold-plating.

### Option B — Tie Qdrant scaling to operational signal, keep DB-per-tenant on the roadmap

- Pros: data-driven decisions later; signals to regulated-industry
  adopters that OpenSpine could grow into their requirements.
- Cons: every "could grow into" creates a future pressure point to
  weaken the architecture under real-world load. Every published roadmap
  promise becomes a future maintenance burden.
- **Why not chosen:** committing to a future variant is a quiet
  commitment to maintain compatibility with two architectures forever.
  Not worth it for a market we aren't pursuing.

### Option C — Reject DB-per-tenant + commit to logical isolation everywhere (chosen)

- Pros: one architecture; one mental model; one set of operational
  concerns; the whole codebase can assume logical isolation invariants
  hold; ADR 0002 simplifies; tenancy.md simplifies.
- Cons: closes the regulated-isolation market segment that requires
  physical isolation. Acceptable.

## Consequences

**Positive.**

- One tenancy architecture across self-hosted and SaaS deployments.
- ADR 0002's collection-per-tenant decision becomes a forever decision —
  no future "revisit when tenants > N" tension.
- Migrations run once per installation, not per tenant.
- Service-layer code never has to route to a tenant-specific connection
  pool.
- Adopters know the answer: "Will you support our regulator's
  physical-isolation requirement?" — "No, run it self-hosted; you'll
  have one tenant per installation, and the database will be physically
  yours by virtue of being on your own server."

**Negative.**

- Closes the SaaS-multi-tenant-with-physical-isolation segment. We don't
  serve customers whose regulator rejects logical isolation when the
  customer is on a shared Postgres.
- A SaaS hoster running OpenSpine at scale (1000+ tenants per
  installation) will hit operational ceilings on collection-per-tenant
  Qdrant. That's their operational problem to solve; we won't change
  the architecture to make it easier.

**Must remain true for this decision to hold.**

- The typical deployment remains single-tenant per installation. If
  that ever flips (e.g., OpenSpine itself launches a SaaS), this ADR
  is revisited.
- RLS continues to be a credible isolation primitive in PostgreSQL.
  Postgres regression here would be a Postgres-wide problem, not
  OpenSpine-specific.

## References

- `docs/identity/tenancy.md` — three-layer isolation model.
- `docs/decisions/0002-qdrant-over-pgvector.md` — to be amended in line
  with this ADR's removal of the ~500-tenant revisit clause.
- `ARCHITECTURE.md` §10 — non-negotiables. This ADR adds a
  "logical isolation only" stance to the spirit of those commitments.
