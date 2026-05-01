# Data model — conventions and cross-module rules

This document captures the database conventions every OpenSpine table follows.
Per-entity schemas live in the module specs; this is the cross-cutting
contract. Anything new added to the schema MUST conform.

## Table prefixes

| Prefix | Module | Owner |
|--------|--------|-------|
| `id_*` | Identity, RBAC, audit | identity-expert |
| `md_*` | Master Data | md-expert |
| `fin_*` | Financial Accounting (universal journal — shared with CO, ADR 0003) | fico-expert |
| `co_*` | Controlling master data (cost/profit centres, internal orders, allocations) | fico-expert |
| `mm_*` | Materials Management | mm-expert |
| `pp_*` | Production Planning | pp-expert |

**Cross-prefix JOINs are forbidden.** A module that needs another's data calls
the owning module's service. RLS catches the trivial leaks; code review
catches the rest.

## Primary keys

Every table has a single primary-key column named `id` of type `UUID`. New
rows generate the id at insert time using `gen_random_uuid()`. We do not use
auto-incrementing integers anywhere in the business schema:

- UUIDs are non-guessable, which matters when ids appear in URLs.
- UUIDs are merge-friendly across replicas and tenants.
- Sequence-based ids leak business volume to anyone who can read a row.

Composite natural keys (e.g., `(tenant_id, code)`) are enforced as **unique
constraints**, not primary keys. The single-column UUID PK keeps every
foreign-key reference simple and cheap.

## Tenant isolation

Every business row carries `tenant_id UUID NOT NULL` referencing
`id_tenant(id)`. Three layers protect the boundary (`tenancy.md` §"Isolation
mechanics"):

1. **Row-level security.** Every business table has
   `ENABLE ROW LEVEL SECURITY` and a policy of the form:

   ```sql
   CREATE POLICY tenant_isolation ON <table>
     USING (tenant_id = current_setting('openspine.tenant_id')::uuid);
   ```

   The session variable is set by the principal-context middleware on every
   request based on the authenticated principal's tenant. RLS is the safety
   net.

2. **Service-layer filter.** Every service method passes `tenant_id`
   explicitly into queries. RLS catches the bug; the service guards against
   it ever happening.

3. **Qdrant collection per tenant.** The semantic index is partitioned by
   collection (`openspine__<tenant_id>`). Cross-tenant search is impossible
   by construction. See ADR 0002 for the topology decision.

Tables that are explicitly tenant-global (`md_uom`, `md_currency`,
`md_uom_conversion` — global catalogues) are exceptions and carry no
`tenant_id` column. They are documented as global in the entity-level spec
and have no RLS policy.

## Audit columns

Every business table carries:

| Column | Type | Notes |
|--------|------|-------|
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Insert time. Never updated. |
| `created_by` | `UUID NOT NULL` | `id_principal.id`. Set by the service layer; never NULL. |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Touched on every UPDATE via a `BEFORE UPDATE` trigger. |
| `updated_by` | `UUID NOT NULL` | `id_principal.id`. Set by the service on every UPDATE. |
| `version` | `INTEGER NOT NULL DEFAULT 1` | Optimistic-concurrency token. Incremented by the same trigger that touches `updated_at`. Mismatched version on update → `ConflictError`. |

Append-only tables (`fin_document_*`, `id_audit_event`, `id_auth_decision_log`)
carry `created_at` and `created_by` only — there are no updates. Reversals
are new rows with a back-pointer to the original; nothing is ever
destructively modified.

## Soft delete vs hard delete

Default is **soft delete via flag**. Master-data entities carry:

```sql
deleted_at  TIMESTAMPTZ
deleted_by  UUID
```

Both NULL until deletion. Service-layer queries filter `deleted_at IS NULL`
by default; admin/audit endpoints expose the deleted rows with explicit
opt-in.

Hard delete is reserved for:

- GDPR-mandated erasure (anonymisation pass over the principal first;
  business documents preserve the principal's anonymised reference).
- Tenant deletion, after the documented retention window.

Audit-relevant tables (`id_audit_event`, `id_auth_decision_log`,
`fin_document_*`, the universal journal) **never** soft- or hard-delete.
Reversal is a new row.

## Indexing

Every foreign key gets an index. Postgres does not create them automatically;
forgetting one wrecks performance on cascading reads.

Beyond FKs, indexes are added for:

- The most common business lookup pattern documented in the module spec
  (e.g., `md_business_partner` indexed on `(tenant_id, role, status)` for the
  vendor-search hot path).
- Unique constraints (Postgres creates the index automatically).
- Audit-time queries on `created_at` / `updated_at` for time-range scans.

Composite indexes always lead with `tenant_id` so RLS-filtered queries are
covered.

## Numeric types

| Use | Type | Notes |
|-----|------|-------|
| Money amounts | `NUMERIC(19, 4)` | 19 digits total, 4 decimal places. Covers every supported currency including ones that need 4 decimals (some Middle Eastern dinars). |
| FX rates | `NUMERIC(19, 9)` | Rate values can be very small or very large. |
| Quantities (stock, BOM, production) | `NUMERIC(19, 4)` | Same shape as money for predictability; the high precision is rarely used but cheap. |
| Counters, boolean flags, small enums | native types | `INTEGER`, `BOOLEAN`, `TEXT`. |

We do not use `FLOAT` / `DOUBLE PRECISION` for any business-relevant value.
Binary floating-point is wrong for accounting.

## Strings and enumerations

- All string columns are `TEXT` — no `VARCHAR(N)` size limits in the schema.
  Postgres treats `VARCHAR(N)` as `TEXT` with a check constraint and there's
  no perf benefit. If a length limit is genuinely a business rule, document
  it in the module spec; don't bury it in the column type.
- Enumerations live as **lookup tables** (e.g., `md_currency`,
  `co_order_status`), not as `CHECK` constraints or `CREATE TYPE … ENUM`.
  Plugins extend them; we'd be undermining that if enum values lived in DDL.

## Time

- All timestamps are `TIMESTAMPTZ` (timezone-aware). Never `TIMESTAMP`
  (naive).
- All times are stored in UTC. Display-time conversion happens in the
  application layer based on tenant / user preferences.
- Date-only fields (`document_date`, `posting_date`, `valid_from`,
  `valid_to`) are `DATE`. They have no timezone; "the document is dated
  2026-05-01 in the company's local fiscal calendar".

## Relationships

- `ON DELETE` defaults to `RESTRICT`. We almost never want a delete to
  cascade across module boundaries. The exceptions are local-scope rows
  (e.g., `md_bp_address` rows cascade-delete when their `md_business_partner`
  parent is hard-deleted) and are documented per-entity.
- `ON UPDATE` is always `RESTRICT`. We do not rely on PK changes; this is a
  defensive default.

## Custom fields (plugin extensions)

Plugins extend standard entities by adding columns, never new tables tied to
core. The columns are namespaced:

```
ext_<plugin_id>_<field_name>
```

For example, `ext_acme_turkish_tax_office` is an Acme plugin's custom field
on `md_business_partner`. The naming prevents collisions and makes plugin
ownership readable from the schema.

The plugin's migration adds the column; the core service's serialisation
layer picks it up automatically because the column is in the entity's
table. The plugin manifest declares which extension targets are populated
(see ARCHITECTURE.md §6.5).

## Migrations

- Driven by Alembic (`alembic/`).
- Every migration is a single-purpose change with a descriptive `slug`.
- Down-migrations are written for every up-migration except destructive data
  migrations (loss-of-information moves are forward-only by design).
- No data migrations land without a smoke test on a representative
  fixture.
- RLS policy changes go in their own migration so they're easy to review.

The migration chain is always linear (no merge/branch revisions). PRs that
introduce parallel branches are rebased before merge.

## Schema-introspection invariants

These are enforced by tests and CI:

1. Every business table has `tenant_id UUID NOT NULL` (excepting documented
   global catalogues).
2. Every business table has the audit columns (`created_at`, `created_by`,
   `updated_at`, `updated_by`, `version`).
3. Every business table has RLS enabled and a tenant-isolation policy
   (excepting documented globals).
4. Every foreign key has an index.
5. No table outside the documented prefixes exists.
6. No `VARCHAR(N)` column types in the schema.

The tests live in `tests/test_schema_invariants.py` (lands alongside the
first real schema in v0.1 §4.2).

## Data-quality conventions

- **Codes are case-preserving.** `BR01` and `br01` are different. Display-time
  normalisation is the application's job.
- **Email and URL columns are stored verbatim.** No lower-casing at insert.
  Normalisation, if needed, is a service-layer convention, not a column
  trigger.
- **Phone numbers are stored as E.164 strings** when validation is possible;
  otherwise as the user entered them with a `phone_format` flag.

## Why this strictness

The schema is the longest-lived part of an ERP. Application code can be
rewritten in a quarter; schemas become migration mountains. Every convention
above is in the doc because either we've decided it explicitly (UUID PKs,
universal journal), or we've seen the cost of getting it wrong elsewhere
(VARCHAR sizes, naive timestamps, missing FK indexes, scattered audit
columns).

If a future change wants to break one of these conventions, that's an ADR.
