# MM — Materials Management

## 1. Purpose

Materials Management runs the **procure-to-pay** flow. From "we need something" to "the invoice is posted and we paid the vendor", MM covers purchase requisitions, purchase orders, goods receipts, invoice verification, inventory bookkeeping, and valuation. MM posts to FI at every goods or invoice event, carries CO dimensions on every commitment and consumption, and consumes master data at every turn.

In a trading business, MM is the heartbeat. In a manufacturing business, MM feeds PP.

## 2. Scope — Phase 1

| Sub-area | In scope |
|----------|----------|
| **Purchase Requisition** | Manual PR, PR from MRP, item categories (standard, service-light, stock), release strategy (simple approval) |
| **Purchase Order** | Standard PO, subcontract PO (without component tracking in v0.3), service-line PO, blanket order (basic), release strategy |
| **Source determination** | Source list, info record (vendor-material conditions), quota arrangement (basic) |
| **Goods Receipt** | GR for PO, GR for reservation (PP component), GR without reference, reversal |
| **Invoice Verification** | Three-way match (PO / GR / IR), tolerance checks, blocking reasons, IR reversal |
| **Inventory Management** | Stock quantities per material/plant/storage location/stock type, goods movements universal posting |
| **Valuation** | Moving average price, standard price; price control per material; stock value always derivable |
| **Physical Inventory** | Count document, count entry, difference posting |
| **Stock transfers** | Plant-to-plant transfer (two-step), storage-location transfer |

## 3. Scope — explicitly deferred

| Deferred | Reason |
|----------|--------|
| **Consignment stock** | Flows work but ownership modelling is complex; defer to v0.4.x or plugin. |
| **Subcontracting with component provisioning tracking** | PO type supported; the physical component flow is a stub in v0.3, full in v0.4. |
| **Stock transport orders (STO) with delivery + goods movement chain** | Simple transfer postings cover Phase 1; STO comes with SD. |
| **Split valuation** (valuation type per batch/origin) | Deferred — requires batch master. |
| **Batch management** | Part of QM; deferred. |
| **Serial numbers** | Deferred. |
| **Foreign trade / customs** | Localisation plugin. |
| **Contract management** (outline agreements with call-off) | Deferred; basic blanket orders in v0.3. |
| **Complex workflow with multiple approvers and conditional routing** | Simple release strategy in Phase 1; full BPMN-style workflow is a plugin concern. |

## 4. Core entities

Tables use the `mm_` prefix.

| Table | Purpose |
|-------|---------|
| `mm_purchase_req` | PR header. Source (manual / MRP / plugin), status, release state, requester. |
| `mm_purchase_req_item` | PR line. Material / short text, quantity, delivery date, plant, storage location, account assignment (cost centre / order / asset / project). |
| `mm_purchase_order` | PO header. Vendor (BP), purchasing org, purchasing group, currency, payment term, release state. |
| `mm_purchase_order_item` | PO line. Material, quantity ordered, price, delivery date, plant, account assignment, tax code, confirmation status. |
| `mm_po_history` | Posting history against PO line — GR documents, IR documents, returns. |
| `mm_goods_movement` | Universal movement document. Movement type (GR, GI, transfer, scrap), material, quantity, plant, storage location, stock type, reference document. |
| `mm_inventory_balance` | Current stock per material / plant / storage location / stock type / valuation type. Kept consistent with `mm_goods_movement` by the posting service. |
| `mm_invoice_receipt` | IR header. Vendor, document date, reference, gross/tax/net amount, block status. |
| `mm_invoice_receipt_item` | IR line referencing PO / GR line with match result. |
| `mm_tolerance_group` | Tolerance configuration for IR three-way match. |
| `mm_release_strategy` | Strategy definition — when required (by amount / plant / material group), steps, approvers. |
| `mm_release_state` | State of a specific document in a strategy. |
| `mm_source_list` | Approved sources per material / plant / validity window. |
| `mm_info_record` | Vendor-material conditions — price, lead time, min/max order qty, info category. |
| `mm_quota_arrangement` | Quota split across multiple sources for the same material. |
| `mm_physical_inventory_doc` | PI document header. |
| `mm_physical_inventory_count` | Count entry per line. |
| `mm_gr_ir_account` | GR/IR clearing account — balance of "goods received, not yet invoiced". |

## 5. Key transactions / business processes

- **Create PR** — manual (requester) or automated (MRP planned order conversion, reservation conversion, plugin).
- **Release PR** — passes through release strategy; becomes ready for conversion.
- **Convert PR to PO** — agent or human selects source (info record / source list / quota), creates PO.
- **Release PO** — approval chain; once released, PO can be sent to vendor.
- **Send PO to vendor** — output management → email / EDI / portal (pluggable).
- **Vendor confirmation** — optional; records promised delivery date per PO line.
- **Post Goods Receipt** — against PO line; updates inventory, posts to stock account (debit) and GR/IR clearing (credit) in FI. Runs hooks.
- **Post Invoice Receipt** — against PO line / GR line; three-way match (price, quantity, tolerance). On match: posts vendor payable (credit) and GR/IR clearing (debit), tax line. On mismatch within tolerance: blocks invoice.
- **Reverse GR / IR** — with audit trail.
- **Transfer stock** — between storage locations (one-step) or between plants (two-step).
- **Run physical inventory** — freeze stock, count, enter counts, post differences.
- **Consumption posting** — goods issue for a cost centre / order / project / reservation; posts inventory credit and expense debit.

## 6. Integrations

| Reads from | What |
|------------|------|
| Master Data | Material, BP (vendor), Plant, Storage Location, Purchasing Org/Group, UoM, currency, tax code |
| FI | Posting period state, GR/IR account, vendor reconciliation account |
| CO | Cost centre / order / profit centre — validated on each line |
| PP | Reservations (for component consumption in production) |

| Publishes events | Consumers |
|-----------------|-----------|
| `mm.purchase_req.created` | Reporting, MRP (close reservation), plugins |
| `mm.purchase_req.released` | Reporting, buyer agents |
| `mm.purchase_order.created` | Reporting, vendor portal, CO (commitment update) |
| `mm.purchase_order.released` | Vendor output, embedding worker |
| `mm.goods_receipt.posted` | FI (already posted), PP (production order component consumed), reporting, embedding worker |
| `mm.invoice_receipt.posted` | FI (vendor payable), reporting |
| `mm.stock.changed` | Reporting, MRP, replenishment agents |
| `mm.physical_inventory.difference_posted` | FI (adjustment posting), controlling |

## 7. Hook points exposed

| Hook | Fires | Can abort? |
|------|-------|------------|
| `purchase_req.pre_release` | Before PR release | Yes |
| `purchase_req.post_release` | After release | No |
| `purchase_order.pre_create` | Before PO saved | Yes |
| `purchase_order.post_create` | After create | No |
| `purchase_order.pre_release` | Before release | Yes |
| `purchase_order.post_release` | After release | No |
| `goods_receipt.pre_post` | Before GR posting commits | Yes |
| `goods_receipt.post_post` | After GR posted | No |
| `invoice_receipt.pre_post` | Before IR posting commits (after `fi_document.pre_post`) | Yes |
| `invoice_receipt.post_post` | After IR posted | No |
| `invoice_receipt.pre_block` | Before IR is blocked for tolerance | Yes |
| `stock.pre_movement` | Before any movement posts | Yes |
| `physical_inventory.pre_post_difference` | Before difference posting | Yes |
| `physical_inventory.post_post_difference` | After difference posted | No |

## 8. AI agent affordances

- **Procurement copilot.** "I need 500 kg of stainless steel by Friday" becomes: agent searches info records + source lists + approved vendors + lead times, proposes 1–3 sources ranked by cost/lead-time/performance, drafts PR, asks for confirmation.
- **Three-way match exception handler.** For IRs blocked for tolerance: agent analyses the cause (price variance? quantity variance? tax mismatch?), checks vendor history, suggests resolution (accept, request credit memo, escalate). Auto-resolves trivial cases (rounding).
- **Spend analysis agent.** Cross-vendor spend clustering — "you are buying the same material from three vendors at different prices, here is the consolidation opportunity".
- **PR-to-PO conversion agent.** For routine PRs, fully autonomous conversion: source determination + price + lead time + tax + account assignment, posts the PO at `pre_create` with evidence.
- **Stock anomaly detector.** Semantic + statistical anomaly detection over `mm_goods_movement` — unusual consumption patterns, suspect adjustments, duplicate receipts.
- **Vendor performance scorer.** On-time, in-full, price stability, invoice accuracy — a composite score agent maintains continuously and exposes for source determination.
- **Inventory optimiser.** Recommends min/max/safety stock given consumption history, lead time variance, service-level target.

## 9. Open questions

1. **Services vs goods split.** SAP separates service masters (ASN) and service-line item procurement. We lean on a simpler model — services are materials with a flag, or text-only items on PO. Revisit if painful.
2. **Valuation class vs GL account determination.** SAP's OBYC is powerful but painful. We probably want a simpler, declarative determination table — `(movement_type, valuation_class, stock_type) → GL account` — with plugin override.
3. **Stock type model.** Unrestricted, blocked, quality inspection, consignment — how many do we ship in v0.3? Unrestricted + blocked + quality-inspection-stub is probably enough.
4. **Release strategy language.** Declarative YAML / DB-table rules vs code-in-plugin? Leaning declarative with plugin escape hatch.
5. **GR/IR clearing cadence.** Automatic on match vs periodic batch? SAP does both. Prefer automatic with override.
6. **Standard price vs moving average — how do we handle revaluation when a standard-priced material is revalued?** Well-trodden — post variance to price-difference account. Document the pattern clearly.
