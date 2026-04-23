# PP — Production Planning

## 1. Purpose

Production Planning turns demand into made product. PP owns the **plan-to-produce** flow: what to make, when, from what components, on which equipment, and what it costs. It consumes master data (materials, plants, work centres), reads MM (stock, open POs), runs planning (MRP) to generate proposed orders and purchase requisitions, executes production orders through release / confirmation / goods receipt, and posts actuals to FI / CO.

PP is where OpenSpine earns its keep for manufacturing customers. Done well, it removes the need for standalone MES or APS systems for mid-market operations.

## 2. Scope — Phase 1

| Sub-area | In scope |
|----------|----------|
| **Bill of Materials (BOM)** | Multi-level BOM, usage (production, engineering), alternatives, validity dating |
| **Routing** | Operations with sequences, work centres, standard values (setup, machine, labour, teardown) |
| **Work Centre master** | Capacity header, formulas for operation duration, cost centre link, default activity types |
| **MRP run** | Net change + regenerative, single-plant, single-level and multi-level BOM explosion, planning file |
| **Production Order** | Create, release, print / dispatch, confirm operations, GR from production, close / TECO |
| **Component handling** | Reservations at release, goods issue (back-flush or discrete) |
| **Confirmations** | Partial / final confirmation per operation, scrap, activity quantities |
| **Capacity** | Load reporting (read-only); no levelling algorithm in Phase 1 |
| **Costing on order** | Planned cost calculation at release, actual cost accumulation, variance at TECO / settlement |

## 3. Scope — explicitly deferred

| Deferred | Reason |
|----------|--------|
| **Process orders** (process industry — recipes, batches) | Deep and distinct from discrete manufacturing. Phase 2. |
| **Repetitive manufacturing** | Deferred to v0.5+. |
| **Kanban** | Phase 2; replenishment plugin could cover mid-term. |
| **Capacity levelling / sequencing** | Planning algorithms are their own project. Read-only load view in Phase 1. |
| **Variant configuration (VC)** | Complex; deferred. |
| **Engineering change management (ECM)** | Basic validity dating only; full ECM deferred. |
| **Shop-floor integration (MES, OPC-UA)** | Integration via plugins, not core. |
| **Collaboration (sub-contracting with multi-site visibility)** | Deferred. |
| **Product costing cost component split beyond simple layers** | Basic material + activity breakdown only. |

## 4. Core entities

Tables use the `pp_` prefix.

| Table | Purpose |
|-------|---------|
| `pp_work_centre` | Work centre master. Plant, cost centre link, capacity category. |
| `pp_work_centre_capacity` | Available capacity per capacity category (machine, labour), shift model. |
| `pp_work_centre_activity` | Activity types produced by this work centre (internal tariff for CO). |
| `pp_bom_header` | BOM header — material, plant, usage, alternative BOM, status, validity window. |
| `pp_bom_item` | BOM line — component material, quantity per, UoM, item category (stock, non-stock, phantom), validity. |
| `pp_routing_header` | Routing header — material, plant, usage, alternative, validity. |
| `pp_routing_operation` | Operation — sequence number, work centre, control key (backflush, milestone), standard values. |
| `pp_routing_sequence` | Alternative / parallel sequences of operations. |
| `pp_planned_order` | Output of MRP — material, quantity, start / finish dates, source (in-house / external). |
| `pp_production_order` | Production order header — material, quantity, BOM / routing reference, status, cost-collect flag, dates. |
| `pp_production_order_operation` | Operations on the order (copied from routing at release, then editable). |
| `pp_production_order_component` | Components on the order (copied from BOM at release, then editable). |
| `pp_production_order_confirmation` | Confirmation documents — yield, scrap, activity quantities, operation status. |
| `pp_mrp_run` | Run log — scope (plant / material), timestamp, number of changes, duration, outcome. |
| `pp_planning_file` | Planning file entries — materials flagged for next MRP run. |
| `pp_reservation` | Reservation from production order to a stock quantity; consumed by MM goods issue. |
| `pp_order_type` | Order type config — number range, settlement profile, costing variant, status profile. |

## 5. Key transactions / business processes

- **Maintain BOM / Routing / Work Centre.** Master data lifecycle; validity-based change management.
- **Run MRP.** Reads demand (sales forecast, planned independent requirements, dependent requirements from higher-level BOM explosion, reservations, stock, open POs). Produces planned orders (in-house) and purchase requisitions (bought-in). Respects lot-sizing rules.
- **Review MRP output.** Planner views planned orders and exceptions (late, missing source, missing BOM).
- **Convert planned order → production order.** Copies BOM and routing into the order; reserves components; creates operations.
- **Release order.** Runs availability check, creates reservations, calculates planned cost, prints shop papers, enables confirmations.
- **Issue components.** Either by explicit goods issue (discrete) or back-flush at operation confirmation.
- **Confirm operation.** Yield + scrap + activity quantities. Posts activity consumption to CO (against the order). Optional back-flush posts component consumption to FI/stock.
- **Post GR from production.** Finished material received into stock; posts inventory debit and production-order credit; closes the physical side of the order.
- **Calculate variance.** At TECO — difference between planned and actual cost per variance category (input price, input qty, lot size, etc.).
- **Settle production order.** Posts accumulated cost from order to material (if moving average) or to price-difference account (if standard), or to a settlement receiver (for order types that settle elsewhere).
- **TECO / close order.** State transitions: `CRTD → REL → PCNF → CNF → DLV → TECO → CLSD`.

## 6. Integrations

| Reads from | What |
|------------|------|
| Master Data | Material, plant, factory calendar, UoM |
| MM | Stock situation, open POs (for MRP), goods issue service (for component consumption), GR service (for finished product) |
| CO | Cost centre, activity type, internal order, costing variant |
| FI | Posting periods |

| Publishes events | Consumers |
|-----------------|-----------|
| `pp.bom.saved` | Embedding worker, MRP (planning file flag) |
| `pp.routing.saved` | Embedding worker |
| `pp.mrp.run_completed` | Reporting, buyer agents, planner dashboards |
| `pp.planned_order.created` | Reporting, planner agents |
| `pp.production_order.released` | MM (reservation creation), reporting |
| `pp.production_order.confirmed` | CO (activity consumption), FI (if back-flush → goods issue posted), reporting |
| `pp.production_order.goods_receipt_posted` | FI, MM (inventory update), settlement |
| `pp.production_order.settled` | FI (variance postings), asset master (if settle-to-asset), CO |

## 7. Hook points exposed

| Hook | Fires | Can abort? |
|------|-------|------------|
| `bom.pre_save` | Before BOM save | Yes |
| `bom.post_save` | After save | No |
| `routing.pre_save` | Before routing save | Yes |
| `routing.post_save` | After save | No |
| `work_centre.pre_save` | Before save | Yes |
| `mrp.pre_run` | Before MRP run starts | Yes |
| `mrp.post_run` | After MRP run ends | No |
| `planned_order.post_convert` | After conversion to production order | No |
| `production_order.pre_release` | Before release (runs availability check etc.) | Yes |
| `production_order.post_release` | After release | No |
| `production_order.pre_confirm` | Before confirmation posts | Yes |
| `production_order.post_confirm` | After confirmation | No |
| `production_order.pre_teco` | Before technical completion | Yes |
| `production_order.pre_settle` | Before settlement | Yes |
| `production_order.post_settle` | After settlement | No |

## 8. AI agent affordances

- **MRP exception reviewer.** After every MRP run, agent triages exception messages (late, material shortage, missing source, missing routing) and groups them by likely root cause. Proposes actions: expedite an open PO, convert a planned order early, move a date.
- **Production planner copilot.** "Schedule these three orders across this week's two shifts" — agent reads work-centre load, finds feasible windows, returns a proposal.
- **BOM change impact analysis.** "If I replace component X with Y on material M, what production orders in flight are affected, what is the cost impact, what customers could this delay?"
- **Confirmation quality check.** Agent watches confirmation patterns — detects a machine reporting impossible yields, a shift with systematically missing confirmations, scrap rates creeping up.
- **Costing narrative.** For each closed order, agent generates "what happened" narrative — planned cost breakdown, actual cost breakdown, major variances with explanations (price variance on component A + efficiency variance on operation 20 + scrap on operation 30).
- **Shop-floor operator interface.** For the floor: a conversational UI to confirm operations, report problems (machine down, material shortage), request help. All actions go through the same service layer with the same audit trail.

## 9. Open questions

1. **How deep on capacity planning in Phase 1?** Read-only load view is our current plan. The moment we add levelling we are in APS territory, and that is a separate project. Leaning: load view only, levelling via plugin or v0.5+.
2. **Variant configuration.** Even minimal VC is a big investment. Probably defer entirely — customers with VC needs can wait or build a plugin.
3. **Back-flush vs discrete issue default.** Back-flush is operationally simpler but hides problems until it is too late. Discrete is SAP's default for a reason. Leaning discrete with back-flush opt-in per control key.
4. **Costing variant count.** One is enough for Phase 1 (`PPC1` — production cost). Multi-version costing (plan / forecast / standard) is later.
5. **Phantom assemblies.** Supported in Phase 1 — they simplify engineering BOMs considerably. Ensure MRP explodes through them correctly.
6. **Reservation-to-goods-issue pull logic.** Which module "owns" the reservation life cycle? Reservation header is PP; physical inventory movement is MM. Service boundary must be crisp.
7. **Multi-plant BOM sharing.** SAP allows a BOM to be flagged as valid across plants. Useful, but many mid-market customers copy per plant. Leaning: per-plant by default, optional "group BOM" for larger customers.
