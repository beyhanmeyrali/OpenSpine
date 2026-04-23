# MD — Master Data

## 1. Purpose

Master Data is the foundation every other module stands on. It defines the organisational units a company operates within, the business partners it transacts with, the materials and services it buys, makes, and sells, the chart of accounts it books to, and the calendars and currencies it measures with. Every transaction in FI, CO, MM, and PP references master data. Getting this right is non-negotiable — mistakes here propagate everywhere.

## 2. Scope — Phase 1

| Sub-area | In scope |
|----------|----------|
| **Organisational structure** | Tenant, Company Code, Controlling Area, Plant, Storage Location, Purchasing Organisation, Purchasing Group |
| **Business Partner** | Unified BP model with role flags (customer, vendor, employee stub). Addresses, bank details, communication. |
| **Chart of Accounts** | Operational chart, account groups, GL account master (P&L and balance sheet), field status groups |
| **Material Master** | Basic data, purchasing view, sales view (minimal), accounting view, storage view. Material types, industry sector. |
| **Units of Measure** | Base UoM, conversion factors, alternative UoMs per material |
| **Currencies & FX** | Currency master, exchange rate types (M, B, G), daily / monthly rates |
| **Number ranges** | Centralised number range service used by every entity that needs IDs |
| **Calendars** | Factory calendar, fiscal year variants, posting period variants |
| **Document types & output** | Cross-module document type registry, basic output control |

## 3. Scope — explicitly deferred

| Deferred | Reason |
|----------|--------|
| Full HR master (org chart, payroll) | HR is out of Phase 1. BP `employee` role covers the stub we need for authorship, approvers, operators. |
| Asset master | Asset Accounting comes post-v0.4. |
| Equipment and functional location | Plant Maintenance is Phase 2+. |
| Batch and serial master | Quality Management adjacent, deferred. |
| Customer pricing condition records | Covered minimally via `info_record` in MM for vendor side; sales-side pricing waits for SD. |
| Variant configuration (VC) | Deep feature, deferred. |
| Routing / BOM master | Owned by PP, not MD. Listed here only because some ERPs bundle them; we separate. |

## 4. Core entities

Tables use the `md_` prefix. This is representative, not exhaustive — detailed schema lives in [`/docs/architecture/data-model.md`](../architecture/data-model.md) once written.

| Table | Purpose |
|-------|---------|
| `md_tenant` | Top-level isolation boundary. Every other row carries `tenant_id`. |
| `md_company_code` | Legal entity. Books are closed at this level. Has its own currency, CoA assignment, fiscal year variant. |
| `md_controlling_area` | Management accounting scope. One or more Company Codes assigned. Single currency for internal reporting. |
| `md_plant` | Physical or logical site. Belongs to one Company Code. |
| `md_storage_location` | Stocking point within a Plant. |
| `md_purchasing_org` | Buying unit. Can span Plants. |
| `md_purchasing_group` | Buyer team / individual responsibility. |
| `md_business_partner` | Unified customer / vendor / employee record. Role flags determine which transactional tables it appears in. |
| `md_bp_role` | BP roles: `customer`, `vendor`, `employee`, `prospect`. A BP can hold multiple roles over time. |
| `md_bp_address` | Addresses attached to a BP, typed (legal, shipping, billing). |
| `md_bp_bank` | Bank accounts attached to a BP, for AP/AR payment processing. |
| `md_chart_of_accounts` | CoA header. |
| `md_gl_account` | GL account master. Per CoA. |
| `md_gl_account_company` | Company-code-specific GL properties (reconciliation indicator, currency, tax code defaults). |
| `md_account_group` | Account grouping (e.g. `ASSETS`, `LIABILITIES`, `REVENUES`). |
| `md_material` | Material master header — basic data valid across plants. |
| `md_material_plant` | Plant-level material extensions (procurement type, MRP parameters). |
| `md_material_valuation` | Valuation-area-level (valuation class, price control, price). |
| `md_material_uom` | Alternative UoM per material with conversion. |
| `md_uom` | Global UoM catalogue. |
| `md_uom_conversion` | Base UoM → alternative UoM conversions (global, not material-specific). |
| `md_currency` | ISO 4217 currencies with decimals. |
| `md_exchange_rate_type` | Rate types (`M` average, `B` bank-selling, `G` bank-buying). |
| `md_fx_rate` | Daily rates per pair per rate type. |
| `md_number_range` | Number range objects with ranges per object / year / Company Code. |
| `md_fiscal_year_variant` | Defines number of periods and how calendar maps to fiscal periods. |
| `md_posting_period` | Open/closed state per period per Company Code per account range. |
| `md_factory_calendar` | Working days and holidays. |

## 5. Key transactions / business processes

- **Onboard tenant** — create tenant, create first Company Code, assign CoA and fiscal year variant, seed factory calendar, open first period.
- **Create Company Code** — with local currency, parallel currencies, CoA, fiscal year variant, posting period variant.
- **Create Business Partner** — create BP, attach roles (vendor / customer / employee), capture addresses, bank, tax numbers.
- **Create Material** — with material type, industry sector, base UoM, plant extension, valuation extension.
- **Upload exchange rates** — daily job or manual upload.
- **Open / close posting period** — at month-end / year-end.
- **Maintain number ranges** — typically at year change or first configuration.

## 6. Integrations

| Module | How MD serves it |
|--------|------------------|
| FI | GL accounts, CoA, Company Code, BP (as AP/AR subledger), fiscal calendar, currencies |
| CO | Controlling area, BP, GL accounts as cost elements, calendars |
| MM | Material, Plant, Storage Location, BP (vendor), Purchasing Org/Group, UoM |
| PP | Material, Plant, factory calendar, UoM |

MD itself depends on nothing in the business domain. It depends on **Identity** for tenant and user context.

## 7. Hook points exposed

| Hook | Fires | Can abort? |
|------|-------|------------|
| `company_code.pre_save` | Before create/update of `md_company_code` | Yes |
| `company_code.post_save` | After commit | No (async) |
| `business_partner.pre_save` | Before create/update | Yes |
| `business_partner.post_save` | After commit | No |
| `material.pre_save` | Before create/update | Yes |
| `material.post_save` | After commit | No |
| `gl_account.pre_save` | Before create/update | Yes |
| `posting_period.pre_open` | Before period state changes to open | Yes |
| `posting_period.pre_close` | Before period state changes to closed | Yes |
| `fx_rate.post_upload` | After a batch of rates is loaded | No |

Plugins typically use these to enforce custom master data rules (e.g. "every Turkish customer must have a tax office"), auto-populate fields, or cascade changes.

## 8. AI agent affordances

- **Onboarding copilot.** Agent walks an admin through tenant / Company Code / CoA / first BP / first Material creation using natural language, asking only for missing inputs, validating as it goes.
- **Bulk import.** Agent takes a CSV or an export from a legacy ERP and maps it to the OpenSpine master model, surfacing ambiguities as structured questions rather than failures.
- **Data-quality agent.** Continuously scans for duplicate BPs, inconsistent material UoM conversions, GL accounts with no postings, unused number ranges — proposes cleanup actions.
- **Semantic search.** "Show me all Turkish suppliers we pay in EUR" becomes a hybrid search: Qdrant narrows candidates, PostgreSQL confirms facts.
- **Master data translation.** Agents translate between OpenSpine master model and foreign identifiers (e.g. SAP material numbers, Odoo product IDs) for integration scenarios.

## 9. Open questions

1. **Number range policy.** Centralised service per tenant, or per Company Code? Lean is centralised with scoping — needs validation with a real pilot.
2. **Business Partner merge / split.** When two BPs turn out to be the same legal entity, how do we merge without breaking historical documents? Probably "inactive + redirect pointer" rather than physical merge.
3. **Material numbering.** External (user-supplied, validated against pattern) vs internal (system-assigned from number range). Default should probably be internal, but industry matters — retailers want GTIN-based external.
4. **Fiscal year variants with 4-4-5 weeks.** Supported in Phase 1 or calendar-month-only? Deferring likely; most mid-market runs calendar month.
5. **Address normalisation.** Do we depend on an external normalisation service (Google / HERE / open-source) or accept free-form? Probably free-form with optional plugin for normalisation.
6. **Multi-language master data.** Material descriptions in multiple languages — built-in translation table or store single language and let the semantic layer handle it? Leaning built-in because that is how every real ERP customer uses it.
