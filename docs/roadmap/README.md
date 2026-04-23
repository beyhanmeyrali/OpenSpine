# Roadmap

Detailed release milestones. This directory is a stub in pre-alpha — expanded milestone docs (`v0.1-foundation.md`, `v0.2-finance-core.md`, etc.) will be added as each release is scoped.

## High-level milestones

| Version | Theme | Summary |
|---------|-------|---------|
| **v0.1** | Foundation | Identity, tenancy, RBAC. Master Data core: Tenant, Company Code, Chart of Accounts, Business Partner, Material Master. Currencies, FX rates, fiscal year / posting periods. PostgreSQL + Qdrant dual-write pipeline operational. Plugin host scaffolded with one example plugin. |
| **v0.2** | Finance core | FI document posting engine, GL, AP, AR, period management. First user-visible business transactions. CO foundations (cost centre, profit centre, internal order). |
| **v0.3** | Materials Management | PR, PO, GR, IR, inventory, valuation. First plugins shipped as community examples. |
| **v0.4** | Production Planning | BOM, Routing, Work Centre, MRP, production orders, confirmations. End-to-end make-to-stock cycle testable. |
| **v0.5** | AI agent layer | Semantic search UI, agentic document understanding, NL reporting, agent operator for FI/MM/PP. |
| **v1.0** | Production-ready | Hardening, migration tooling, first pilot deployments. Full transactional coverage of Phase 1 modules. |

## Design tenets held constant across releases

1. **Dependencies flow downward.** No release adds a cycle between modules.
2. **Backward-compatible by default.** Breaking a hook contract requires two-release deprecation.
3. **No release ships without docs updated.** `docs/` tracks built reality, not just intent, from v0.1 onwards.
4. **Plugin contracts are stable from the release that introduces them.** If we ship a hook in v0.2, it stays in v1.0 unless deprecated.

## What is NOT in the roadmap (yet)

- HR / HCM module
- Sales & Distribution (beyond AR customer invoices)
- Plant Maintenance, Quality Management
- Asset Accounting (beyond lightweight tracking)
- Consolidation / intercompany elimination
- Country localisation packs (these will be plugins, not core)

These are deliberate deferrals to keep Phase 1 shippable. They return to the roadmap post-v1.0 based on community demand.
