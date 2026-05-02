# Identity & Access

Identity, tenancy, authentication, and authorisation in OpenSpine. These are foundational — every API call in every module answers "who is calling?" and "are they allowed?" by going through this layer.

## Documents

| Doc | Covers |
|-----|--------|
| [tenancy.md](./tenancy.md) | Multi-tenant model, organisational units, data isolation |
| [users.md](./users.md) | Principals: humans, agents, technical accounts. One identity model for all. |
| [authentication.md](./authentication.md) | How principals prove who they are — passwords, SSO, API tokens, agent tokens |
| [roles.md](./roles.md) | Role catalogue — composite and single roles mapped to job functions |
| [permissions.md](./permissions.md) | Authorisation objects, fine-grained permission model |

## Core beliefs

1. **Agents are first-class principals, not backdoors.** An agent has its own identity, its own token, its own audit trail. It calls the same services through the same permission checks as any human.
2. **Multi-tenant by default, single-tenant by configuration.** Every row in every business table carries a `tenant_id`. Single-tenant deployments simply run with one tenant. Multi-tenant deployments isolate through row-level security and service-layer enforcement.
3. **No permission bypass.** Plugins, agents, and admin users never skip permission checks. There is no "run as SUPER" mode. Elevated operations are explicit, audited, and time-bounded.
4. **Auditable by construction.** Every authentication, every authorisation decision, every significant business action is recorded in an append-only audit log keyed by principal, action, target, and timestamp.
5. **Principle of least privilege.** A role has only the permissions the job function requires. Role expansion is proposed, reviewed, and approved — not assumed.
6. **Separation of concerns with a regulatory floor.** Segregation-of-duties (SoD) controls are enforced through a declarative SoD matrix — you cannot have "create vendor" and "release payment" in the same role set without explicit override.

## Audit topology

Three append-only stores answer three different questions. They look related and they share a join key (`trace_id`), but the concerns are genuinely distinct — collapsing them dilutes each.

| Table | Question it answers | Cardinality | Examples |
|-------|--------------------|-------------|----------|
| `id_audit_event` | **What happened?** Every authentication and every significant business action. The "who did what to what" log. | One row per business action or auth event | `auth.login.success`, `auth.token.issued`, `business_data.invoice.created`, `business_data.invoice.reversed`, `system.role.assigned` |
| `id_auth_decision_log` | **What was allowed or denied?** Every check the auth-object engine performs, with the qualifier values evaluated and the rule that decided. | One *or many* rows per business action — a single operation can trigger N permission checks | `fi.invoice:post:allow{cc=DE01,amount=5000}`, `fi.payment:release:deny{reason=amount_exceeds_limit,attempted=15000,allowed=10000}`, `mm.purchase_order:release:sod_block{conflicting_role=AP_PAYMENT_RELEASE}` |
| `id_agent_decision_trace` | **Why did the agent do what it did?** The reasoning trace behind an agentic action — semantic recall results, structured verifications, candidates considered, the chosen path. | One per agent action; may live in a separate store for volume | "Selected vendor V-019 because: (a) Qdrant ranked 3 candidates for 'stainless steel 304'; (b) PG verified V-019 in budget and in info-record; (c) chose highest on-time score." |

### How they fit together

Every request entering OpenSpine carries a `trace_id` from the
authentication middleware (or generates one if none is present). That
`trace_id` is propagated through OpenTelemetry context across the entire
operation — service calls, hook dispatches, event-bus publishes, the
embedding worker, plugin handlers — and is written into every audit
record.

A single agent-driven AP invoice posting therefore writes:

- **One** `id_audit_event` row recording `business_data.ap_invoice.created`.
- **Several** `id_auth_decision_log` rows: one per qualifier-checked
  permission inside the operation (e.g., `fi.invoice:post`, plus checks
  on the cost-centre assignment, plus the document-type permission).
- **One** `id_agent_decision_trace` row capturing the agent's reasoning
  for choosing this vendor, this GL account, this cost centre.

All three rows carry the same `trace_id`. A compliance reviewer asking
"show me everything about this transaction" runs `WHERE trace_id = ?`
across the three tables and gets a complete picture.

### Retention defaults

| Table | Default retention | Notes |
|-------|------------------|-------|
| `id_audit_event` | 7 years | Aligned with most jurisdictions' financial-records retention. |
| `id_auth_decision_log` | 1 year | High volume; longer retention available per tenant policy. |
| `id_agent_decision_trace` | 1 year | High volume (LLM traces are large); may live in cold storage with reference from `id_audit_event`. |

Tenant configuration can extend any of these. None can be shorter than
the regulatory floor for the jurisdiction the tenant operates in.

### What goes where (rule of thumb)

- "I need to know **what changed**" — `id_audit_event`.
- "I need to know **why this was allowed/denied**" — `id_auth_decision_log`.
- "I need to know **why an agent chose this path**" — `id_agent_decision_trace`.

If a future event seems to fit two of the three, default to writing it
to the most-specific store. The three are append-only; over-logging is
recoverable, under-logging is not.

## Table prefix

All identity tables use the `id_` prefix. Identity is its own module — it is referenced by every business module but depends on no business module.
