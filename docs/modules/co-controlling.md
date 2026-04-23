# CO — Controlling

## 1. Purpose

Controlling is **management accounting**. Where FI answers "what is the legal financial state of the company", CO answers "how is the business performing, by cost centre, by profit centre, by product, by order?". CO shares the universal journal with FI — every FI posting already carries cost centre, profit centre, and internal order dimensions. CO owns the master data for those dimensions, the allocations that move cost between them, and the management reporting that sits on top.

There is no reconciliation problem between FI and CO because there is no separate ledger. This is the S/4HANA insight and we adopt it from day one.

## 2. Scope — Phase 1

| Sub-area | In scope |
|----------|----------|
| **Cost Centre Accounting** | Cost centre master, hierarchy, standard hierarchy per Controlling Area, group reporting |
| **Profit Centre Accounting** | Profit centre master, hierarchy, dummy profit centre, derivation rules |
| **Internal Orders** | Real internal orders for event-based cost collection (projects, marketing campaigns, capex). Order types, status profile, settlement rule. |
| **Cost Elements** | Reused from GL accounts (primary) and locally defined secondary cost elements (for allocations) |
| **Simple allocations** | Assessment (one-to-many with assessment cost element), Distribution (primary cost element preserved). Cycles with senders/receivers. |
| **Planning** | Version-based cost centre plan (at least current year). Actual vs plan variance. |
| **Statistical key figures** | For allocation drivers (headcount, square metres) |

## 3. Scope — explicitly deferred

| Deferred | Reason |
|----------|--------|
| **Product Costing (PC)** | Tightly coupled to PP. Deferred to v0.4 when PP lands. |
| **Profitability Analysis (CO-PA)** | Major module on its own. Basic profit-centre reporting covers Phase 1. |
| **Activity-Based Costing** beyond simple activity types | Deferred. |
| **Overhead calculation with templates** | Deferred. |
| **Top-down distribution, nested cycles** | Phase 2. |
| **Actual costing / material ledger** | Deferred. Moving-average valuation from MM is the Phase 1 answer. |
| **Cost object hierarchies** | Deferred. |

## 4. Core entities

Tables use the `co_` prefix for CO-owned master data. Transactional CO data lives on `fin_document_line` — CO does not maintain its own separate postings table.

| Table | Purpose |
|-------|---------|
| `co_cost_centre` | Cost centre master. Belongs to Controlling Area. Category (production, service, admin), validity dates, responsible person (BP). |
| `co_cost_centre_hierarchy` | Standard hierarchy nodes and alternative groupings. |
| `co_profit_centre` | Profit centre master. Belongs to Controlling Area. |
| `co_profit_centre_hierarchy` | Standard hierarchy + alternative groupings. |
| `co_internal_order` | Internal order header. Order type, status, settlement rule, currency, validity. |
| `co_order_type` | Configuration of order types — number range, default values, statuses. |
| `co_order_status` | Status catalogue (`CRTD` created, `REL` released, `TECO` technically closed, `CLSD` closed). |
| `co_allocation_cycle` | Allocation definitions — senders, receivers, tracing factor type. |
| `co_allocation_segment` | Segment within a cycle (one segment = one allocation rule). |
| `co_statistical_key_figure` | Key figures used as allocation drivers (e.g. headcount). |
| `co_skf_value` | Period values for each statistical key figure per cost object. |
| `co_version` | Plan / actual / variant versions. `0` = actual, `1..n` = plans. |
| `co_cost_element` | Secondary cost elements (primary ones reuse `md_gl_account` where `pl_indicator = P&L`). |
| `co_derivation_rule` | Rules to derive profit centre from cost centre, from material, from BP. |

## 5. Key transactions / business processes

- **Maintain cost centre master.** Create, change, lock, validity-date changes. Hook fires at each.
- **Maintain profit centre master.** Same shape.
- **Create internal order.** Often triggered by a project or capex approval. Settles to cost centre / asset / profit centre per settlement rule.
- **Release internal order.** State transition — the order is now postable.
- **Technical completion (TECO).** Order blocks further postings but may still receive settlement.
- **Maintain allocation cycle.** Define senders (e.g. IT cost centre), receivers (all production cost centres), tracing factor (headcount).
- **Execute allocation cycle (actual or plan).** Generates `fin_document_line` rows with secondary cost elements, balancing senders to zero (assessment) or distributing proportionally (distribution).
- **Plan costs.** Manual or upload plan values per cost centre / cost element / period / version.
- **Settle internal order.** Periodic settlement posts accumulated costs from the order to its receivers per the settlement rule. Generates standard FI / CO document.
- **Month-end run.** Execute allocations → settle orders → revaluation → variance calculation.

## 6. Integrations

| Reads from | What |
|------------|------|
| Master Data | Controlling Area, Company Code, BP (as cost centre manager), GL accounts |
| FI | Every primary posting — CO actuals are just the subset of `fin_document_line` with a cost object |
| MM | PO commitments carry cost assignment; GR consumption likewise |
| PP | Production order actuals, variances, settlement |

| Publishes events | Consumers |
|-----------------|-----------|
| `co.cost_centre.saved` | Embedding worker, derivation rule refresh |
| `co.internal_order.released` | MM (now postable), reporting |
| `co.allocation.executed` | Reporting, FI (postings already created) |
| `co.settlement.executed` | Reporting, asset master (when settling to asset) |

## 7. Hook points exposed

| Hook | Fires | Can abort? |
|------|-------|------------|
| `cost_centre.pre_save` | Before create/update | Yes |
| `cost_centre.post_save` | After commit | No |
| `profit_centre.pre_save` | Before create/update | Yes |
| `profit_centre.post_save` | After commit | No |
| `internal_order.pre_release` | Before release | Yes |
| `internal_order.post_release` | After release | No |
| `internal_order.pre_teco` | Before TECO | Yes |
| `allocation.pre_run` | Before cycle executes | Yes |
| `allocation.post_run` | After cycle executes | No |
| `settlement.pre_run` | Before order settlement | Yes |
| `settlement.post_run` | After order settlement | No |

## 8. AI agent affordances

- **Cost driver explanation.** "Why is marketing cost up 18% vs plan this quarter?" — agent joins semantic search (events, comments, attachments on marketing cost centre) with structured drill-down (line items, vendors, trends), returns grounded narrative with citations.
- **Allocation modeller.** Agent converts a natural-language rule ("split IT cost across all production cost centres by headcount") into an allocation cycle definition, previews impact, and offers to persist.
- **Plan vs actual narrative.** Agent generates period commentary for managers — top variances, root-cause hypotheses, suggested corrective actions.
- **Order-level decision support.** For capex orders: agent tracks planned vs actual spend, forecasts final cost based on trajectory, flags orders likely to overrun.
- **Cost assignment suggestion.** When an invoice comes in without a cost centre, agent suggests one based on vendor history, description semantics, and prior postings.

## 9. Open questions

1. **Controlling Area vs Company Code.** One Controlling Area per tenant or allow multiple? Multiple adds complexity (intercompany management accounting). Default should be one CA per tenant; multi-CA as an advanced option.
2. **Group currency.** Where does it live — Company Code or Controlling Area? S/4HANA allows divergence; we lean towards Controlling Area defines the group currency for management reporting.
3. **Derivation rules.** How far do we go — rule-based (IF material_group = 'STEEL' THEN profit_centre = 'PC-001') or hard-coded per-material assignments? Both, probably. Rule-based is a plugin-friendly layer.
4. **Plan version count.** How many versions do mid-market customers need? One plan + one forecast, typically. Support unbounded but index efficiently.
5. **Allocation granularity.** Header-level vs line-level allocation source — SAP does both. We probably default to line-level and let customers pick simpler if they want.
6. **Does CO need its own reporting UI module, or is it all served by the shared reporting layer?** Leaning shared reporting layer — CO contributes semantic metadata so the generic reporting can produce cost-centre-shaped views.
