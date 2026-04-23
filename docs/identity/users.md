# Users — Principals

## One identity model

Every actor in OpenSpine is a **principal**. There are three kinds:

| Kind | Description | Example |
|------|-------------|---------|
| **Human user** | A person who authenticates with a credential (password, SSO, passkey). | Amina Yılmaz, GL accountant |
| **Agent** | An AI agent authorised to act autonomously within bounded scope. | `ap-autoposter-v3` |
| **Technical account** | A service-to-service identity used for integrations, scheduled jobs, plugin infrastructure. | `integration-svc-netsuite-bridge` |

All three share the same table, the same token model, the same permission checks, and the same audit trail. A human approving an invoice and an agent approving an invoice go through identical validation — the only difference is who the audit log records.

This is a deliberate architectural choice. Many systems treat agents and service accounts as second-class (often elevated) identities. That creates blind spots. OpenSpine refuses that trade.

## What is a principal, precisely?

A principal has:

- **An identity** — the row in `id_principal`.
- **One or more credentials** — password, SSO federation, API token, agent token. Credentials are separate from identity; one principal can have many, each with its own lifetime.
- **Role assignments** — which composite / single roles they hold (see [roles.md](./roles.md)).
- **Authorisation scopes** — the org-unit ranges they may operate within (e.g. Company Code `DE01`, Plants `1000`–`1099`).
- **An audit trail** — every significant action taken through this principal.

## Humans

Human users authenticate and are assumed to be present (or, when session is active, "present enough"). Sessions are time-boxed. Re-authentication is required for sensitive operations (SoD-breaking, release of large payments, tenant configuration).

Humans can be:
- **Employees** of the tenant's company
- **External collaborators** — vendors self-servicing, customers placing orders, auditors reading books

The principal model does not care which — permission scopes do.

## Agents

An agent is a principal with:

- **A scoped token** with explicit permission whitelist (e.g. "`ap_invoice.create` within Company Code DE01, amount ≤ 10 000 EUR, only for pre-approved vendors")
- **An expiry** — tokens are short-lived by default (hours to days)
- **A provenance chain** — every agent token was provisioned by some human principal, and that link is recorded. If an agent does something unexpected, we know who provisioned it.
- **A decision log** — agents record not just *what* they did (same as humans) but *why* — the reasoning trace behind the action. This is a unique agent affordance.

Agents are never privileged beyond their scope. There is no "agent superuser". If an agent needs broader scope, a human principal with that scope must approve the scope increase.

## Technical accounts

Used for:
- Integration middleware (inbound webhooks from third-party systems)
- Scheduled jobs (MRP run, FX rate refresh, period-end automation)
- Plugin infrastructure services

A technical account is just a non-human principal with a long-lived API token and a narrowly scoped role. It is audited identically.

## Lifecycle

| Event | Applies to | Notes |
|-------|-----------|-------|
| **Create** | All | Humans onboarded via invitation flow; agents created by authorised principal with explicit scope; technical accounts as part of integration setup. |
| **Activate** | Humans | First credential set. |
| **Suspend** | All | Login blocked, existing sessions invalidated. Keeps principal row for audit. |
| **Delete** | Rare | Anonymise the principal row (retain audit). True delete only for GDPR-mandated erasure, even then preserving aggregate audit metadata. |
| **Rotate credentials** | All | Routine for tokens; required on suspicion of compromise. |

## Core tables

| Table | Purpose |
|-------|---------|
| `id_principal` | The principal row — type (human / agent / technical), display name, tenant, status, created_at, created_by. |
| `id_human_profile` | Human-specific fields — email, locale, time zone, employee_bp_id (link to BP), manager_principal_id. |
| `id_agent_profile` | Agent-specific fields — model, version, provisioner_principal_id, purpose, constraint summary. |
| `id_credential` | Credentials — one-to-many from principal. Type (password_hash, sso_federation, api_token, agent_token), status, expires_at. |
| `id_session` | Active sessions for humans. |
| `id_token` | Issued tokens (agent / API) — hashed, last-used, revocation state. |
| `id_audit_event` | Append-only audit log — principal_id, action, target, timestamp, outcome, trace_id. |
| `id_agent_decision_trace` | Agent decision reasoning — links from audit event to the reasoning record (may live in a separate store for volume). |

## Principal-to-Business-Partner link

A human user who is an employee links to their Business Partner record in Master Data (`md_business_partner` with `employee` role). This lets the system surface "who created this document" consistently with how vendors / customers are identified.

Agents and technical accounts do **not** have a BP link — they are system identities, not business entities.

## Open questions

1. **Passkeys from day one?** WebAuthn / passkey auth is table-stakes in 2026. Leaning yes for humans; SSO / SAML / OIDC also required.
2. **Agent model versioning.** Track the model ID and version per agent token so audit trails remain interpretable across upgrades.
3. **Delegation.** Can Amina delegate "approve AP invoices for one week" to Bora? Probably yes — modelled as a time-bounded role grant, with the delegation itself audited.
4. **Multi-factor for agents?** Non-sensical for most cases, but sensitive agents (tenant admin automation) might require a second human co-sign at token issuance time.
