# FI — Financial Accounting

## 1. Purpose

Financial Accounting is the **legal ledger**. It answers "what is the true financial state of this company, at this moment, under this accounting framework?" FI owns the General Ledger and the subsidiary ledgers for Accounts Payable and Accounts Receivable, the document posting engine that enforces double-entry, the calendar of fiscal periods, and the reconciliation boundaries. Every other module eventually posts to FI, because eventually everything becomes money.

FI is authoritative. If FI disagrees with another system, FI is right and the other system is wrong until reconciled.

## 2. Scope — Phase 1

| Sub-area | In scope |
|----------|----------|
| **General Ledger** | Universal journal posting, double-entry enforcement, multi-currency (local, group, hard), parallel ledgers (leading IFRS + local GAAP) |
| **Accounts Payable** | Vendor invoices, credit memos, payment proposals (manual run), open item management, clearing |
| **Accounts Receivable** | Customer invoices (from AR, not yet a sales order), credit memos, incoming payments, open item management, clearing |
| **Document posting engine** | Document types, posting keys (Dr/Cr abstraction), validation & substitution, automatic account determination |
| **Period management** | Open / close posting periods per account range per Company Code; year-end carry-forward |
| **Tax** | Tax codes, tax jurisdictions, tax lines on documents; calculation via pluggable tax engine |
| **Foreign currency** | Revaluation of open items, realised and unrealised FX gain/loss |
| **Reversals & corrections** | Document reversal with audit trail; no silent edits |

## 3. Scope — explicitly deferred

| Deferred | Reason |
|----------|--------|
| **Asset Accounting (AA)** | Depreciation, asset lifecycle — planned post-v0.4. |
| **Bank Accounting** (auto-matching, electronic bank statement import) | Manual clearing in Phase 1; EBS is a plugin or v0.5 feature. |
| **Dunning** | Full dunning procedure deferred; a stub table is created for plugins. |
| **Tax declarations / country-specific e-invoicing** | Deferred to localisation plugins (per-country). |
| **Intercompany elimination and consolidation** | Deferred to v1.x. |
| **Profit centre P&L (CO-PCA reporting)** | Covered in CO for reporting, not duplicated in FI. |
| **Cost-of-sales accounting split** | Deferred; Phase 1 is period accounting by default. |

## 4. Core entities

Tables use the `fin_` prefix. FI and CO share the posting tables — CO dimensions (cost centre, profit centre, internal order) are columns on every FI line.

| Table | Purpose |
|-------|---------|
| `fin_document_header` | One row per posted business document. Type, posting date, document date, entry date, reference, header text, reversal pointer, status. |
| `fin_document_line` | **The universal journal.** One row per debit/credit line. Carries GL account, amount in local/document/group currency, tax line, BP (AP/AR), cost centre, profit centre, internal order, project, segment, ledger group. |
| `fin_document_type` | Document type catalogue: `SA` (GL posting), `KR` (vendor invoice), `DR` (customer invoice), `KZ` (vendor payment), `DZ` (customer payment), `AB` (reversal), plus custom. Each type carries default posting key, number range, account type restrictions, reversal type. |
| `fin_posting_key` | Debit / credit + account-type + business-transaction abstraction. Largely internal plumbing. |
| `fin_ledger` | Leading + non-leading ledgers (e.g. `0L` leading IFRS, `2L` local GAAP). Every line belongs to a ledger group. |
| `fin_open_item` | Materialised view over open AP/AR items from `fin_document_line`. |
| `fin_clearing` | Clearing documents that close open items. One clearing can match many open items. |
| `fin_tax_code` | Tax codes per Company Code — jurisdiction, rate, input/output, reporting category. |
| `fin_tax_jurisdiction` | Jurisdictional hierarchy for territorial taxes (US sales tax, Brazilian ICMS, etc.). |
| `fin_payment_term` | Term keys — net days, discount tiers, grace days. |
| `fin_payment_method` | Methods (bank transfer, check, ACH, SEPA, card). |
| `fin_dunning_procedure` | Stub — plugins can populate. |
| `fin_substitution_rule` | Validation and substitution rules applied at post time. |

## 5. Key transactions / business processes

- **Post GL document.** Direct journal entry. Validates balanced per currency per ledger, validates period open, runs `fi_document.pre_post` hooks, commits, runs `fi_document.post_post` hooks.
- **Post vendor invoice (AP).** Creates payable in `fin_document_line` against reconciliation account of the vendor's BP, generates tax lines, creates open item.
- **Post customer invoice (AR).** Mirror of AP.
- **Clear open items.** Match one or many open items against a clearing document (typically a payment). Partial clearing supported.
- **Post vendor payment.** Outgoing payment; clears one or many open AP items; updates bank sub-ledger.
- **Post customer receipt.** Incoming payment; clears one or many open AR items.
- **Reverse document.** Creates a reversing document referencing the original; both flagged as reversed/reversal. Original is never deleted.
- **Run period-end close.** Runs revaluation (FX), clearing cleanup, optionally posts accruals, closes periods.
- **Year-end close.** Carry-forward balances; open new fiscal year periods.

## 6. Integrations

| Reads from | What |
|------------|------|
| Master Data | GL accounts, Company Code, CoA, BP, currencies, tax codes, posting periods |
| CO | Validates cost centre / profit centre / order assignments on posting |

| Publishes events | Consumers |
|-----------------|-----------|
| `finance.document.posted` | Embedding Worker (semantic index), CO (derives allocations), plugins |
| `finance.document.reversed` | Same consumers as posted |
| `finance.open_item.cleared` | Reporting, AP/AR agents, dunning plugins |
| `finance.period.closed` | Reporting, audit, downstream roll-ups |

| Posted to by | What they post |
|--------------|---------------|
| MM | Goods receipt (GR/IR clearing, stock), invoice receipt (payable) |
| PP | Production confirmation (WIP, variance on settlement) |
| MD | Exchange rate differences on revaluation |
| Plugins | Anything — always through the FI posting service, never direct INSERT |

## 7. Hook points exposed

| Hook | Fires | Can abort? |
|------|-------|------------|
| `fi_document.pre_post` | Before the document posting transaction commits | Yes |
| `fi_document.post_post` | After commit | No (async) |
| `fi_document.pre_reverse` | Before reversal | Yes |
| `ap_invoice.pre_post` | Before AP invoice commits (runs after `fi_document.pre_post`) | Yes |
| `ap_invoice.post_post` | After AP invoice committed | No |
| `ar_invoice.pre_post` | Before AR invoice commits | Yes |
| `ar_invoice.post_post` | After AR invoice committed | No |
| `open_item.pre_clear` | Before clearing commits | Yes |
| `open_item.post_clear` | After clearing | No |
| `period_close.pre_run` | Before period-end close begins | Yes |
| `period_close.post_run` | After period-end close | No |
| `year_end.pre_carryforward` | Before balance carry-forward | Yes |

## 8. AI agent affordances

- **Invoice from PDF / email.** Agent extracts header and lines, maps to BP, GL, tax code, cost assignments. Calls `ap_invoice.pre_post` via the posting service; returns confidence score and reasoning. Human reviewer approves unless auto-post threshold met.
- **Document explainer.** Agent produces plain-English narrative of any journal: what it means, which processes it ties to, what downstream effects it has. Uses embeddings on historical context.
- **Period-close assistant.** Pre-close checks — unposted recurring entries, unposted goods receipts awaiting invoice, parked documents, FX revaluation preview, posting period status. Walks the accountant through each.
- **Anomaly detection.** Clusters postings semantically and statistically; flags outliers (unusual amounts, unusual GL / cost centre combinations, duplicate invoices).
- **Intercompany reconciler.** Cross-Company Code postings; agent identifies mismatched pairs and suggests correcting entries.
- **Natural-language reporting.** "Show me gross margin by profit centre YTD vs. last year" — agent translates to structured queries over `fin_document_line` with ledger and period scoping, returns tabular + narrative output with citations.

## 9. Open questions

1. **Parallel ledger model.** S/4HANA-style (one universal journal, ledger column per line) is our default. But does every customer run parallel ledgers? The added complexity may not justify the simplicity cost for pure-local-GAAP shops. Possible answer: one ledger by default, additional ledgers opt-in at Company Code level.
2. **Tax engine.** Built-in simple engine (rate × base, jurisdictional lookup) or plugin-only (SAP-like tax determination procedures)? Lean is built-in simple + plugin hook for complex cases.
3. **Parked vs posted documents.** Do we support "parked" (saved but not posted) documents as a first-class state? Yes — workflow and approval routing need it. Requires a state lifecycle on `fin_document_header`.
4. **Audit requirements (SOX, GoBD, SAF-T).** Built-in immutable audit log is non-negotiable. Country-specific exports are plugins.
5. **Number assignment timing.** Document number at post time (gap-free per number range) or at entry time (gaps allowed)? Gap-free is a regulatory requirement in several jurisdictions — default should support it, but be configurable.
6. **Retrospective corrections.** In closed periods, what is our policy? SAP forces you to open the period or post to current with a reference. We should default to the latter, reverse-and-repost to current period, with clear audit linkage.
