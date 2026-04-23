# Tenancy

## Model

OpenSpine is **multi-tenant from day one**. A single-tenant deployment is just a multi-tenant deployment with one tenant.

A **tenant** is the top-level isolation boundary. Every business row — every material, invoice, cost centre, user — carries a `tenant_id`. Tenants do not share data. A query from one tenant cannot see rows from another.

Within a tenant, the organisational hierarchy mirrors the structure a mid-market to enterprise business actually uses:

```
Tenant
 └── Controlling Area              (management accounting scope)
      └── Company Code             (legal entity — books are closed here)
           └── Plant               (physical or logical site)
                └── Storage Location (stocking point within a plant)
```

- A **Company Code** belongs to exactly one Tenant and exactly one Controlling Area.
- A **Plant** belongs to exactly one Company Code.
- **Purchasing Organisations** and **Purchasing Groups** are orthogonal to the plant hierarchy — a Purchasing Org can serve multiple Plants.

## Why multi-tenant by default

1. **Simpler hosting story.** A SaaS deployment is identical to a self-hosted one, save the number of tenants.
2. **Partner / subsidiary use cases.** Mid-sized groups with several legal entities share infrastructure but not data. Multi-tenant is the default for them.
3. **Sandboxes.** A customer can spin up a test tenant alongside production in the same installation without forking.

## Isolation mechanics

Three complementary layers, any one of which would keep tenants apart. Defence in depth:

1. **Row-level security (RLS) in PostgreSQL.** Every business table has an RLS policy scoped by `tenant_id = current_setting('openspine.tenant_id')`. The service layer sets this session variable on every request based on the authenticated principal.
2. **Service-layer enforcement.** Every domain service filters by tenant before executing queries. RLS is the safety net; the service is the first check.
3. **Qdrant collections per tenant.** The semantic index stores vectors in tenant-scoped collections or tenant-keyed payloads. Cross-tenant search is impossible by construction.

## Tenant lifecycle

- **Create tenant.** Admin action. Creates empty schemas, seeds Chart-of-Accounts templates (optional), creates first admin user.
- **Suspend tenant.** Read-only state. Users can read, not write. Used during disputes, migrations, or billing issues.
- **Archive tenant.** Data exported, retained per retention policy, removed from hot storage.
- **Delete tenant.** Hard delete across all tables and Qdrant collections. Requires explicit confirmation and a waiting period.

## Identity scoping

Users and agents belong to **one tenant** — a user cannot log into two tenants with the same credential. If a consultant serves multiple tenants, they hold multiple identities (one per tenant) linked by an out-of-band profile. This avoids the entire category of cross-tenant access bugs.

API tokens and agent tokens are tenant-scoped; the tenant is either embedded in the token or derived during issuance and never mutable at use time.

## Org units as authorisation scope

Authorisation can be scoped **at any level of the hierarchy**:

- *"Amina is GL Accountant for Company Code DE01."*
- *"Carlos is Buyer for Purchasing Organisation NORAM across all Plants."*
- *"Agent `ap-autoposter-v3` can only post invoices for Plants 1000 and 1100 with amounts ≤ 10 000 EUR."*

See [roles.md](./roles.md) and [permissions.md](./permissions.md) for how this scoping is expressed.

## Core tables

| Table | Purpose |
|-------|---------|
| `id_tenant` | Tenant master — name, status (active / suspended / archived), created timestamp, plan / deployment metadata. |
| `id_tenant_setting` | Per-tenant configuration (time zone, default language, feature flags). |

Organisational units proper (Company Code, Controlling Area, Plant, etc.) live in Master Data (`md_*`) because they are business objects — but identity uses them as authorisation scopes.

## Open questions

1. **Database-per-tenant vs shared schema?** Default: shared schema with RLS. For regulated workloads, a plugin / deployment variant may want database-per-tenant — cleaner isolation, harder ops. Revisit.
2. **Cross-tenant consolidation.** Group-level reporting across tenants (e.g. a holding company with per-entity tenants) — how? Probably a separate reporting tenant that pulls from source tenants via authorised read-only APIs. Not Phase 1.
3. **Tenant move.** Exporting and re-importing a tenant cleanly — needs a structured export format. Post-v1.0.
