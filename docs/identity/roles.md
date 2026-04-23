# Roles

## Model

OpenSpine uses a **two-tier role model**, consciously borrowed from SAP because it is what ERP practitioners already understand:

- **Single role.** A cohesive bundle of authorisations for one job activity — e.g. "Post AP invoice".
- **Composite role.** A bundle of single roles representing a job function — e.g. "AP Clerk" = multiple single roles for AP creation, display, clearing, reporting.

A principal is assigned **composite roles** (typically) or single roles (rarely, for edge cases). Composite roles make day-to-day admin practical; single roles make the permission model precise and auditable.

## Why this rather than simple "role = permission bundle"

- Real ERP jobs are combinations. "Accountant" is not one thing — it is a cluster of related responsibilities that vary slightly by company.
- Composite roles allow tenant-level customisation without forking the permission model. Acme can tweak what "AP Clerk" means to them without redefining what "create invoice" means.
- Two tiers make audits readable. "Why does Amina have permission to release payments?" → "Because she holds the composite role `AP_CLERK_SENIOR`, which includes the single role `AP_PAYMENT_RELEASE_UP_TO_10K`."

## Ready-made role catalogue (Phase 1)

Every OpenSpine tenant ships with a curated starter catalogue. Tenants can copy, modify, and extend freely. Naming convention: `<MODULE>_<FUNCTION>_<QUALIFIER>` for single roles; `<MODULE>_<JOB>[_SENIOR|_LEAD]` for composite roles.

### Master Data (MD)

**Composite roles**
- `MD_STEWARD` — master data maintainer
- `MD_ADMIN` — full MD configuration authority
- `MD_VIEWER` — read-only

**Representative single roles**
- `MD_BP_CREATE`, `MD_BP_CHANGE`, `MD_BP_DISPLAY`
- `MD_MATERIAL_CREATE`, `MD_MATERIAL_CHANGE`, `MD_MATERIAL_DISPLAY`
- `MD_GL_ACCOUNT_MAINTAIN`
- `MD_ORG_UNIT_MAINTAIN` (Company Code, Plant, etc. — sensitive)
- `MD_FX_RATE_UPLOAD`
- `MD_POSTING_PERIOD_MAINTAIN`

### Financial Accounting (FI)

**Composite roles**
- `FI_GL_ACCOUNTANT` — posts and reviews GL entries
- `FI_AP_CLERK` — handles payable side (create / change / clear)
- `FI_AP_CLERK_SENIOR` — AP clerk + payment release up to threshold
- `FI_AR_CLERK` — handles receivable side
- `FI_AR_CLERK_SENIOR` — AR clerk + dunning release
- `FI_CONTROLLER` — read everything in FI, close periods, run reports
- `FI_AUDITOR` — read-only, with broad scope across all Company Codes
- `FI_VIEWER` — standard read-only

**Representative single roles**
- `FI_GL_POST`, `FI_GL_REVERSE`, `FI_GL_DISPLAY`
- `FI_AP_INVOICE_POST`, `FI_AP_INVOICE_PARK`, `FI_AP_PAYMENT_PROPOSAL`, `FI_AP_PAYMENT_RELEASE`
- `FI_AR_INVOICE_POST`, `FI_AR_RECEIPT_POST`, `FI_AR_CLEARING`
- `FI_PERIOD_OPEN`, `FI_PERIOD_CLOSE`
- `FI_YEAR_END_CARRYFORWARD`

### Controlling (CO)

**Composite roles**
- `CO_COST_CENTRE_MANAGER` — maintains a cost centre, reviews actuals vs plan
- `CO_CONTROLLER` — broad CO authority, allocations, settlements
- `CO_PLANNER` — maintains plans (budgets)
- `CO_VIEWER` — read-only

**Representative single roles**
- `CO_COST_CENTRE_MAINTAIN`, `CO_PROFIT_CENTRE_MAINTAIN`
- `CO_INTERNAL_ORDER_CREATE`, `CO_INTERNAL_ORDER_RELEASE`, `CO_INTERNAL_ORDER_SETTLE`
- `CO_ALLOCATION_DEFINE`, `CO_ALLOCATION_RUN`
- `CO_PLAN_ENTER`, `CO_PLAN_LOCK`

### Materials Management (MM)

**Composite roles**
- `MM_REQUESTER` — employee who creates PRs (narrow)
- `MM_BUYER` — converts PRs, creates POs, handles vendors
- `MM_BUYER_SENIOR` — buyer + release strategy authority
- `MM_WAREHOUSE_OPERATOR` — posts goods movements, handles physical inventory
- `MM_INVOICE_CLERK` — verifies IR, manages blocks (not the same person as AP clerk)
- `MM_INVENTORY_CONTROLLER` — read stock, run valuations, approve adjustments
- `MM_VIEWER` — read-only

**Representative single roles**
- `MM_PR_CREATE`, `MM_PR_CHANGE`, `MM_PR_RELEASE`
- `MM_PO_CREATE`, `MM_PO_RELEASE`, `MM_PO_CLOSE`
- `MM_GR_POST`, `MM_GR_REVERSE`
- `MM_IR_POST`, `MM_IR_BLOCK`, `MM_IR_UNBLOCK`
- `MM_STOCK_MOVEMENT_POST`, `MM_STOCK_TRANSFER`, `MM_STOCK_ADJUSTMENT`
- `MM_PHYSICAL_INVENTORY_COUNT`, `MM_PHYSICAL_INVENTORY_POST_DIFFERENCE`

### Production Planning (PP)

**Composite roles**
- `PP_PLANNER` — runs MRP, reviews exceptions, converts planned orders
- `PP_ROUTING_MAINTAINER` — maintains BOM, routing, work centres
- `PP_SHOP_FLOOR_OPERATOR` — confirms operations, reports problems
- `PP_FOREMAN` — shop-floor supervisor — order release, re-dispatch
- `PP_VIEWER` — read-only

**Representative single roles**
- `PP_BOM_MAINTAIN`, `PP_ROUTING_MAINTAIN`, `PP_WORK_CENTRE_MAINTAIN`
- `PP_MRP_RUN`, `PP_PLANNED_ORDER_CONVERT`
- `PP_PRODUCTION_ORDER_CREATE`, `PP_PRODUCTION_ORDER_RELEASE`
- `PP_PRODUCTION_ORDER_CONFIRM`, `PP_PRODUCTION_ORDER_TECO`, `PP_PRODUCTION_ORDER_SETTLE`

### System / cross-cutting

**Composite roles**
- `SYSTEM_TENANT_ADMIN` — configures the tenant, creates users, manages roles
- `SYSTEM_AUDIT_READER` — read-only access to `id_audit_event` across the tenant
- `SYSTEM_PLUGIN_ADMIN` — install / configure / disable plugins
- `SYSTEM_INTEGRATION_ADMIN` — manage technical accounts and API tokens
- `SYSTEM_AI_OPERATOR` — provision and manage agent principals

**Representative single roles**
- `USER_CREATE`, `USER_SUSPEND`, `USER_DELETE`
- `ROLE_ASSIGN`, `ROLE_DEFINE`
- `TOKEN_ISSUE`, `TOKEN_REVOKE`
- `PLUGIN_INSTALL`, `PLUGIN_CONFIGURE`, `PLUGIN_DISABLE`
- `AUDIT_READ_ALL`

## Scope qualifiers

A role assignment is not complete without scope. Every principal-to-role binding carries:

- **Company Code scope** — list, range, or wildcard
- **Plant / Storage Location scope** — list, range, or wildcard
- **Purchasing Org scope** — list, range, or wildcard (for MM roles)
- **Amount limits** — for `_RELEASE` / `_APPROVE` single roles, a per-assignment amount ceiling in a defined currency
- **Validity window** — optional `valid_from` and `valid_to`

Example binding: `(Amina, FI_AP_CLERK_SENIOR, company_codes=[DE01,DE02], amount_limit=10000 EUR, valid_to=2026-12-31)`.

## Segregation of Duties (SoD)

OpenSpine ships with a baseline SoD matrix identifying forbidden role combinations. Examples:

- `FI_AP_CLERK` + `FI_AP_PAYMENT_RELEASE` on the same principal + same scope — someone cannot create and pay the same invoice
- `MD_BP_CREATE` + `FI_AP_PAYMENT_RELEASE` — cannot create a vendor and pay them
- `MM_GR_POST` + `MM_IR_POST` — cannot post both halves of the three-way match

Violations can be **blocked** (hard policy) or **warned + audited** (soft policy with explicit override requiring approver). Tenant admins configure which.

## Core tables

| Table | Purpose |
|-------|---------|
| `id_role_single` | Single role definitions — name, description, module, system-or-tenant-owned. |
| `id_role_single_permission` | Single role → permission mapping. |
| `id_role_composite` | Composite role definitions. |
| `id_role_composite_member` | Composite → single role membership. |
| `id_principal_role` | Role assignments — principal, role_id, scope qualifiers, validity. |
| `id_sod_rule` | SoD constraints — forbidden combinations with severity. |
| `id_sod_override` | Audited overrides when soft policy is relaxed. |

## Role change lifecycle

- **System roles** (ship with OpenSpine) are immutable by design. Tenants cannot redefine `FI_AP_INVOICE_POST` to mean something different. They can copy to a tenant role, rename, modify.
- **Tenant roles** (created or copied in a tenant) are fully mutable but every change is audited.
- **Role assignment changes** are audited. Every grant, revoke, scope change has a principal and timestamp.

## Open questions

1. **Role hierarchies.** SAP has derivation (derived roles inherit from a master with scope overrides). Powerful but baroque — start with composite-single two-tier and add derivation only if pain emerges.
2. **Attribute-based access control (ABAC).** For certain scenarios (geography, data sensitivity), ABAC is cleaner. Could blend — most things RBAC, some things ABAC-like via permission predicates. Decide case by case.
3. **Time-bounded elevation.** "Emergency access" — granting a SoD-violating role for one hour with heavy audit. Build in from day one? Probably yes as a `SYSTEM_EMERGENCY_ACCESS` single role that requires dual approval.
4. **Agent roles.** Agents draw from the same catalogue; but agent-specific roles (e.g. `AP_AUTOPOST_AGENT`) can be crafted for narrow scope. Convention: prefix with module, suffix with `_AGENT`.
