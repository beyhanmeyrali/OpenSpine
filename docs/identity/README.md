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

## Table prefix

All identity tables use the `id_` prefix. Identity is its own module — it is referenced by every business module but depends on no business module.
