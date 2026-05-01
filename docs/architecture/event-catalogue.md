# Event catalogue

Every event published on the OpenSpine event bus, with its producer,
canonical consumers, payload shape, and stability commitments.

The bus is **at-least-once** delivery (`ARCHITECTURE.md` §8). Consumers must
be idempotent on `event_id`. The default backbone is Redis Streams. Consumer
groups give us per-consumer offsets and replayability.

## Event envelope

Every event on the bus shares this envelope (see
`src/openspine/core/events.py`):

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `stream` | string | yes | Routing key. Format: `<module>.<entity>.<verb>`. |
| `tenant_id` | string (UUID) | yes | The tenant whose data this event concerns. Subscribers MUST filter by tenant when fanning out per-tenant work (e.g., per-tenant Qdrant collections). |
| `event_id` | string (UUID v4) | yes | Globally unique. Consumers dedupe on this. |
| `occurred_at` | string (ISO-8601 UTC) | yes | When the producing service committed. |
| `payload` | object | yes | Event-specific. Schema is per-event (below). |
| `trace_id` | string | no | OpenTelemetry trace id. Propagated when present. |
| `span_id` | string | no | OpenTelemetry span id of the producing operation. |

## Stream-name conventions

```
<module>.<entity>.<verb>
```

- `<module>` is one of: `master_data`, `finance`, `controlling`, `mm`, `pp`,
  `id`, `system`.
- `<entity>` is the snake-cased entity name (e.g., `business_partner`,
  `purchase_order`, `production_order`).
- `<verb>` is past tense (`created`, `updated`, `posted`, `released`, etc.).

Pattern subscribers use `*` for a single segment and `**` for one-or-more
trailing segments — e.g., `master_data.**` matches every event in MD.

## v0.1 events

Producer modules and consumers per the v0.1 plan and module specs.

### Master Data

| Stream | Producer | Consumers | Payload |
|--------|----------|-----------|---------|
| `master_data.tenant.created` | MD service | embedding worker, audit | `{ tenant_id, name, plan, created_by }` |
| `master_data.company_code.created` | MD service | embedding worker, FI bootstrap | `{ company_code_id, tenant_id, code, name, currency, coa_id, fiscal_year_variant_id }` |
| `master_data.company_code.updated` | MD service | embedding worker | same shape minus `code` (immutable) |
| `master_data.business_partner.created` | MD service | embedding worker, plugins | `{ bp_id, tenant_id, roles, primary_address, country, name }` |
| `master_data.business_partner.updated` | MD service | embedding worker, plugins | same |
| `master_data.material.created` | MD service | embedding worker, plugins | `{ material_id, tenant_id, material_type, base_uom, industry_sector }` |
| `master_data.material.updated` | MD service | embedding worker, plugins | same |
| `master_data.gl_account.created` | MD service | embedding worker | `{ gl_account_id, tenant_id, coa_id, account_group, pl_indicator }` |
| `master_data.gl_account.updated` | MD service | embedding worker | same |
| `master_data.fx_rate.uploaded` | MD service | reporting, FI revaluation prep | `{ tenant_id, rate_type, valid_from, count }` |
| `master_data.posting_period.opened` | MD service | reporting, FI | `{ tenant_id, company_code, year, period }` |
| `master_data.posting_period.closed` | MD service | reporting, FI | same |

### Identity (subset emitted in v0.1)

| Stream | Producer | Consumers | Payload |
|--------|----------|-----------|---------|
| `id.principal.created` | identity service | audit, embedding worker | `{ principal_id, tenant_id, kind, display_name, created_by }` |
| `id.principal.suspended` | identity service | audit, session reaper | `{ principal_id, tenant_id, suspended_by, reason }` |
| `id.token.issued` | identity service | audit | `{ token_id, principal_id, tenant_id, scope_summary, expires_at }` |
| `id.token.revoked` | identity service | audit | `{ token_id, principal_id, revoked_by }` |
| `id.role.assigned` | identity service | audit | `{ principal_id, role_id, scope, assigned_by }` |
| `id.sod.violation_blocked` | auth-object engine | audit, security alerting | `{ principal_id, attempted_role, conflicting_role, scope }` |

### System

| Stream | Producer | Consumers | Payload |
|--------|----------|-----------|---------|
| `system.reconciliation.started` | reconciliation job | observability | `{ job_id, scope }` |
| `system.reconciliation.completed` | reconciliation job | observability | `{ job_id, replayed, failed, duration_ms }` |
| `system.plugin.loaded` | plugin host | observability, audit | `{ plugin_id, version, hooks }` |
| `system.plugin.disabled` | plugin host | observability, audit | `{ plugin_id, reason }` |

## Future events (planned per module specs)

These don't exist in v0.1 but are reserved by their module specs. Listed here
so consumers can plan ahead and producers don't accidentally rename them.

- **Finance (v0.2):** `finance.document.posted`, `finance.document.reversed`,
  `finance.open_item.cleared`, `finance.period.closed`.
- **Controlling (v0.2.x):** `co.cost_centre.saved`,
  `co.internal_order.released`, `co.allocation.executed`,
  `co.settlement.executed`.
- **Materials (v0.3):** `mm.purchase_req.created`,
  `mm.purchase_req.released`, `mm.purchase_order.created`,
  `mm.purchase_order.released`, `mm.goods_receipt.posted`,
  `mm.invoice_receipt.posted`, `mm.stock.changed`,
  `mm.physical_inventory.difference_posted`.
- **Production (v0.4):** `pp.bom.saved`, `pp.routing.saved`,
  `pp.mrp.run_completed`, `pp.planned_order.created`,
  `pp.production_order.released`, `pp.production_order.confirmed`,
  `pp.production_order.goods_receipt_posted`,
  `pp.production_order.settled`.

## Stability commitments

- **Stream names are part of the public contract.** Renaming a stream is a
  two-release deprecation cycle — the old name keeps firing alongside the
  new one for one full release before removal.
- **Adding fields to a payload is non-breaking** as long as existing fields
  don't change type or meaning. Consumers ignore unknown fields.
- **Removing or changing the type of an existing payload field** requires a
  deprecation cycle.
- **Adding a new stream is always non-breaking.**

Hooks (the synchronous extension surface) follow the same rules — see
`ARCHITECTURE.md` §6.3.

## Consumer obligations

Consumers MUST:

- Be **idempotent on `event_id`**. The bus delivers at-least-once.
- **Filter by `tenant_id`** when work is per-tenant.
- **Propagate `trace_id` / `span_id`** to downstream OpenTelemetry spans
  when the event carries them. Otherwise correlation breaks across the
  async boundary.
- **Emit `openspine_events_consumed_total{stream,consumer,outcome}`** for
  every event they handle. The metric is the standard observability
  signal for consumer lag and reliability.

Consumers SHOULD:

- Handle their own retries with exponential back-off rather than relying on
  bus-level redelivery.
- Persist a checkpoint so they can resume from the last processed event_id
  on restart (Redis Streams consumer groups handle this natively).

## Operational notes

- **Replay.** The reconciliation job (v0.1) replays events for any entity
  that failed to land in Qdrant. It walks PostgreSQL, synthesises events,
  and publishes them via a dedicated `system.reconciliation.*` stream so
  consumers can distinguish replays from live events when needed.
- **Retention.** Default 7 days for live streams; 30 days for audit-relevant
  streams (`id.*`). Tenant configurations can extend retention.
- **Cardinality.** Per-tenant sub-streams (e.g., a stream per tenant per
  entity) are explicitly avoided. Tenant scoping is a payload field, not
  a stream-name dimension.
