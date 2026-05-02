# 0002 — Qdrant for the semantic index, not pgvector

**Status:** Accepted
**Date:** 2026-05-01
**Deciders:** Project owner, solution-architect council

## Context

OpenSpine's AI-first stance (`ARCHITECTURE.md` §10 #1) requires a semantic index for every business document. Two credible options:

- **Qdrant** — purpose-built vector database, separate process, scales independently.
- **pgvector** — Postgres extension, vectors as columns next to the relational data.

The choice affects operations, scaling, query patterns, and what "consistency" means in this system.

`0001-postgres-as-source-of-truth.md` already establishes Postgres as authoritative; this decision is about where the *derivative* vector index lives.

## Decision

**Qdrant is the default semantic index for OpenSpine.** Postgres + Redis Streams → embedding worker → Qdrant, async, with reconciliation.

A pgvector deployment variant is **explicitly supported** for small single-node installations where running a separate Qdrant cluster is operational overkill. It is a deployment knob, not the default.

## Alternatives considered

### Option A — pgvector as the default

- Pros:
  - One database, one backup, one set of credentials, one HA story.
  - Vectors transactionally consistent with their facts — no event bus, no reconciliation job, no dual-write surface area.
  - Lower bar for self-hosting.
- Cons:
  - **Resource contention.** Vector indexing/querying and OLTP have very different access patterns. Running them on the same instance means tuning is a perpetual compromise; isolating them means running multiple Postgres instances anyway.
  - **Filtering and hybrid search are weaker.** Qdrant's combination of vector + payload filter + scalar/binary quantisation is ahead at scale. pgvector's HNSW index doesn't compose with the rest of Postgres's planner the way you'd hope on filtered queries.
  - **Migration risk.** Starting with pgvector and moving off is harder than starting with Qdrant. The data model, the query API, and the operational story all diverge. We'd be locking customers into a decision that gets harder to reverse over time.
  - **Independent scaling.** Vector workloads grow with embedding count and query rate. Transactional workloads grow with document writes and reads. Decoupling them is the right scaling axis.

### Option B — Qdrant as the default

- Pros:
  - Purpose-built; the operational story under our pattern (shard, replicate, snapshot, scale) is well-trodden.
  - Filtering and hybrid search are first-class, which is exactly what the agent grounding pattern needs.
  - Decoupled from Postgres — vector outages don't take down the transactional path; transactional outages don't degrade query latency.
  - Per-tenant isolation expressed naturally as collections, which helps the multi-tenant story.
- Cons:
  - Two systems to operate.
  - Async indexing means search results can be slightly stale (mitigated by the grounding pattern: agents always verify candidates against Postgres before acting).
  - Reconciliation tooling has to be built and tested — counted in v0.1 scope.

### Option C — pgvector as the default with a "switch to Qdrant later" plan

- Pros: defer the operational decision until scale demands it.
- Cons: see "Migration risk" under Option A. The migration cost grows with installed base. Customers who set up on pgvector and grow into Qdrant scale will pay that cost.
- **Why not chosen:** the migration is the worst time to be making this call. Better to make it once.

## Decision rationale

We choose Option B because:

1. The architectural pattern (Postgres SoT + async event-driven derivative) is exactly what Qdrant is built for. The cost of "two systems" is paid in operational tooling we'd be building anyway for the event bus.
2. Filter-heavy hybrid search is the agent's primary query pattern. Qdrant's filtering composes with vector search natively; pgvector's doesn't, at scale.
3. Per-tenant collections give us hard isolation as a first-class concept, not a payload-filter bolt-on.
4. The fallback (pgvector for small deployments) covers the legitimate case for the smaller end of the market without contaminating the default.

## Multi-tenant collection topology

**Collection-per-tenant. Forever.** This is the architectural commitment, not a "revisit at scale" hedge. See ADR 0005 for the broader tenant-isolation stance: OpenSpine commits to logical isolation only, and the typical deployment is single-tenant per installation — which makes "scale to many tenants per Qdrant cluster" a SaaS-hoster problem rather than a core-architecture problem.

Naming convention: `openspine__<tenant_id>` (lowercase). Implemented in `src/openspine/core/qdrant.py`.

Hosters who run OpenSpine multi-tenant at large scale (hundreds or thousands of tenants per installation) will hit operational ceilings on collection-per-tenant Qdrant. Solving that is their operational concern; the architecture won't be revisited to weaken isolation under their load.

## Consequences

**Positive.**

- Independent scaling of vector and transactional workloads.
- Strong filtering and hybrid search out of the box.
- Per-tenant isolation by collection.
- Default is the right answer for the mid-market and enterprise targets we're optimising for.

**Negative.**

- Two systems means two backup, monitoring, and capacity-planning concerns. Documented in the deployment guide.
- Async indexing means search staleness — bounded by event-bus consumer lag, monitored as a Prometheus metric, mitigated by the grounding pattern.
- Reconciliation tooling is non-trivial to build correctly. Counted in v0.1.

**Must remain true for this decision to hold.**

- Qdrant remains actively developed and operationally credible. If it stagnates or its licence changes adversely, this ADR is revisited — but the dual-write pattern would survive a swap to a different vector store with little churn at the OpenSpine layer (the embedding worker is the only component that talks to Qdrant directly).

## References

- `ARCHITECTURE.md` §1 (system overview), §4 (dual-write pattern), §9 (deployment topology)
- `README.md` §"Architecture" and §"Tech Stack"
- `0001-postgres-as-source-of-truth.md` — the prerequisite decision.
- Qdrant docs on collections, payload filtering, hybrid search.
