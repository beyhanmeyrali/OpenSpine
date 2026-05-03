# NEEDS-INPUT — questions queued for owner

Decisions that came up during the overnight session and the morning
council pass. Once decided, the resolution is captured here, in an ADR,
or in the relevant doc, and the entry is removed.

---

## Resolved on 2026-05-02 (this session)

1. ~~**Hook-naming canonical form.**~~ → ADR 0008 (entity.action; FI's
   `fi_document.*` → `journal_entry.*`).
2. ~~**Audit-log topology.**~~ → New "Audit topology" section in
   `docs/identity/README.md`. Three tables stay; `trace_id` joins.
3. ~~**Database-per-tenant deployment variant.**~~ → ADR 0005:
   rejected forever. Logical isolation only. Default deployment is
   single-tenant per installation.
4. ~~**Qdrant collection topology threshold.**~~ → ADR 0002 amended:
   collection-per-tenant forever; the previously-floated 500-tenant
   revisit clause is dropped. Captured in ADR 0005.
5. ~~**FX-rate `mid` reference.**~~ → `permissions.md` updated: auth
   amount conversions use rate type `M` (average), never `B` or `G`,
   to avoid directional bias.

---

## Still open

### A. AGPL plugin distribution (deferred)

**Status:** owner chose to skip. Not blocking v0.1.

**Question.** AGPL-3.0's network-interaction clause in the context of
plugins running in-process on a network-accessible OpenSpine deployment.
Three plausible legal stances:

- Strict — plugins are part of the combined work; AGPL applies.
- Lenient — plugins are separate works.
- Pragmatic — bundled distribution = combined work; runtime-loaded
  entry-point plugins = separate works.

**Owner direction:** address only when an actual adopter asks.

### B. Identity-core schema review process (§4.2)

**Status:** RESOLVED 2026-05-03. Implementation landed in commits
`7ed4615..0c3bbfc`. The strategic shape was decided as follows:

- **Single `id_token` table** discriminated by `kind`; agent invariants
  enforced via DB-level CHECK (must have `expires_at`,
  `provisioner_principal_id`, and `reason`).
- **`id_tenant` is the global registry** — no `tenant_id`, no RLS;
  service-layer permission gates listing.
- **All other id_* tables RLS-isolated** via
  `tenant_id = current_setting('openspine.tenant_id')::uuid`.
- **Bootstrap cycle** resolved by `DEFERRABLE INITIALLY DEFERRED` FKs
  on the audit-author and tenant-id columns.
- **Audit-trigger pattern** — one shared `_id_touch_updated_audit()`
  plpgsql function attached to every table that has
  `updated_at`/`version`. Append-only `id_audit_event` skips it.
- **Token storage = SHA-256, not argon2id.** Argon2 is for low-entropy
  passwords; for 256-bit cryptographic randoms it costs ~50ms/request
  with no security gain. `authentication.md` updated.
- **Audit-author FKs** (`created_by`, `updated_by`,
  `*_by_principal_id`) are exempt from the "every FK gets an index"
  rule. `data-model.md` updated; schema-invariants test exempts.

If a follow-on review wants a stricter design, the owner can revise
via ADR — but the current shape carries the council's reasoning
inline (see model file docstrings + the ADR-style commit messages).

### C. Cross-tenant consolidation reporting (post-v1.0)

**Status:** genuinely open per `docs/identity/tenancy.md`. Not v0.1.

Group-level reporting across tenants (a holding company with per-entity
tenants). Probably a separate reporting tenant pulling from source
tenants via authorised read-only APIs.

### D. Tenant move (post-v1.0)

**Status:** genuinely open per `docs/identity/tenancy.md`. Not v0.1.

Exporting and re-importing a tenant cleanly. Needs a structured export
format.

---

## What I did NOT touch (and why)

For transparency:

- **No ADR 0004.** Owner directed skip on AGPL legal nuance.
- **No force-push, no history rewrite, no branch deletion.**
- **No new dependencies in `pyproject.toml`** beyond what was declared
  in v0.1 §1.7's observability list.
- **No edits to LICENSE or top-level README narrative.**
- **No changes to module spec docs** beyond:
  - `md-master-data.md` — fixed dangling `data-model.md` reference.
  - `fi-finance.md` — applied the ADR 0008 hook renames.
  - `mm-materials.md` — fixed cross-reference to renamed FI hook.
  - `tenancy.md` — replaced Q1 with the deployment-model + non-negotiable.
  - `permissions.md` — fixed FX rate reference per item 5 above.
  - `identity/README.md` — added Audit topology section.
