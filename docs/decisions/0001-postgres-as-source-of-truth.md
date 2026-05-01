# 0001 — PostgreSQL is the source of truth

**Status:** Accepted
**Date:** 2026-05-01
**Deciders:** Project owner, solution-architect council

## Context

OpenSpine writes business data into two stores: PostgreSQL (transactional facts) and Qdrant (semantic index). Every business document lives in both. We have to be unambiguous about which one is authoritative when they disagree, because they will disagree — eventually-consistent systems always do.

Three forces:

1. **Regulatory and audit reality.** ERP customers are subject to SOX, GoBD, SAF-T, country-specific audit requirements. Auditors expect a single, immutable, queryable, transactional record of what happened. They do not accept "well, the vector store said…".
2. **Operational reality.** Vector indexes go down, get reindexed, change embedding models, get migrated to new infrastructure. Treating them as authoritative would mean every Qdrant operation is now a regulated event.
3. **Engineering reality.** Building the dual-write so that both stores are simultaneously authoritative requires distributed-transaction machinery (2PC, sagas, compensating actions) that is famously brittle and expensive.

## Decision

**PostgreSQL is the single source of truth for all business state.** Qdrant is a derivative index that can be rebuilt from PostgreSQL at any time. When the two disagree, PostgreSQL wins by definition.

Concretely:

- Every business write is committed to PostgreSQL inside a database transaction. The user/agent receives a response only after that COMMIT.
- After COMMIT, an event is published to Redis Streams. An embedding worker consumes the event, generates the vector, and upserts into Qdrant. This is asynchronous and outside the request-response path.
- A reconciliation job replays events for any entity that failed to index, runs hourly by default, and is manually triggerable.
- If Qdrant is lost entirely, reconciliation rebuilds the full index from PostgreSQL. This is a tested scenario, not a theoretical one.
- Reads from Qdrant are always followed by a verification read from PostgreSQL when the result is used to make a business decision (the "semantic recall, then structured verification" pattern in `ARCHITECTURE.md` §7).

## Alternatives considered

### Option A — Treat both as authoritative, using distributed transactions

Two-phase commit across PostgreSQL and Qdrant, or a saga pattern with compensating writes.

- Pros: in theory, the two stores are always consistent.
- Cons: 2PC across heterogeneous stores is a notorious operational nightmare; every Qdrant outage becomes a transactional outage; the engineering cost dwarfs the value; and we'd still need PostgreSQL to be the regulated record for audit purposes.
- **Why not chosen:** the cost is enormous and the audit/regulatory case still wants Postgres-as-truth. Distributed transactions don't actually buy us anything.

### Option B — Use Qdrant (with payload storage) as the only store

Qdrant supports rich payloads. In principle one could store the full business data in Qdrant payloads.

- Pros: one store, no dual-write.
- Cons: Qdrant is not a relational database. No foreign keys, no joins, no complex queries, no transactions across documents, no mature backup tooling, no audit-grade durability. Auditors don't recognise it as a system of record.
- **Why not chosen:** Qdrant is a fine vector index; it's not a system of record.

### Option C — pgvector inside PostgreSQL only

Skip Qdrant entirely; use the `pgvector` extension to keep vectors next to facts.

- Pros: one store; transactional consistency; simpler operations.
- Cons: vector workloads scale very differently from transactional workloads; running both on the same Postgres puts them in resource contention; Qdrant's filtering, hybrid search, and quantisation features are notably ahead of pgvector at scale; a future migration *off* pgvector is harder than starting with the right tool.
- **Why not chosen:** addressed in `0002-qdrant-over-pgvector.md`.

## Consequences

**Positive.**

- One unambiguous source of truth simplifies reasoning, debugging, recovery, and audit.
- Qdrant outages do not block business operations; the user gets their response from Postgres, and the index catches up asynchronously.
- We can swap embedding models, rebuild indexes, or migrate the vector store entirely without touching the business data.
- Auditors get the record they expect.

**Negative.**

- Search results can be momentarily stale (sub-second to seconds in normal operation, longer if the embedding worker is backed up). The agent grounding pattern (semantic recall, then structured verification) accepts this — staleness in candidate retrieval is fine because Postgres delivers the facts.
- Two stores means two operational concerns: backup, monitoring, capacity. Documented in the deployment guide.
- The dual-write requires reconciliation tooling we have to build and test. Counted in v0.1 scope.

**Must remain true for this decision to hold.**

- Postgres remains capable of serving the transactional load. If we ever outgrow a single Postgres cluster, sharding strategy comes before this decision is revisited.
- Reconciliation remains a tractable batch job. If reconciliation can't keep up with steady-state event flow, the dual-write design needs revisiting — but that would be a Qdrant/event-bus issue, not a question of which store is authoritative.

## References

- `ARCHITECTURE.md` §3 (transaction journey), §4 (dual-write pattern), §10 (non-negotiables #3)
- `docs/modules/README.md` — "Authoritative source of truth is PostgreSQL. Qdrant is a derivative."
- `0002-qdrant-over-pgvector.md` — why we run Qdrant alongside Postgres rather than pgvector inside Postgres.
- `0003-universal-journal.md` — the most regulated table set in the system; this decision is what makes the universal journal auditable.
