# OpenSpine

> **The open-source, AI-native ERP.** A free alternative to the legacy enterprise giants, built for the age of intelligent systems.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Status: Pre-Alpha](https://img.shields.io/badge/Status-Pre--Alpha-orange.svg)]()

---

## Why OpenSpine?

Enterprise Resource Planning should not be a privilege of companies that can afford seven-figure licenses. SAP, Oracle, Microsoft Dynamics, and their peers have built extraordinary software — and locked it behind pricing, consultant ecosystems, and deployment complexity that exclude the mid-market, emerging economies, and ambitious smaller enterprises.

OpenSpine exists to change that.

Built from scratch by an enterprise software insider, designed for the AI age from day one, and released under a copyleft license to stay free forever.

## The Vision

- **Free forever.** AGPL-3.0 licensed. No enterprise edition, no paid tier, no bait-and-switch.
- **AI-first, not AI-bolted-on.** AI agents are the first-class users of OpenSpine. Every API endpoint, every data model, every piece of documentation is designed for agent consumption first, human consumption second. Agents don't assist users — agents *are* the users. Human interfaces exist as a secondary layer.
- **Agents as consultants.** In traditional ERPs, implementation requires armies of functional and technical consultants. In OpenSpine, AI agents fill that role — configuring, customising, troubleshooting, and operating the system. Human consultants and operators remain in the loop, but the system is built for agents to drive.
- **Industry-proven, not legacy-burdened.** Established data models and business logic, without the decades of accumulated complexity.
- **Mid-market first, enterprise-ready.** Start small, scale linearly.

## Scope — Phase 1

The initial release focuses on the **spine** of any manufacturing or trading business:

| Module | Scope |
|--------|-------|
| **Finance & Controlling** | General Ledger, Accounts Payable, Accounts Receivable, Asset Accounting, Cost Center & Profit Center Accounting |
| **Materials Management** | Master Data, Purchasing, Inventory Management, Invoice Verification |
| **Production Planning** | BOM, Routings, Work Centers, MRP, Production Orders |

Deliberately **out of scope** for Phase 1: HR, Plant Maintenance, Quality Management, CRM. Those come later, driven by community demand.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React + TypeScript UI                     │
│              (Module-per-domain component library)           │
└────────────────────────────┬────────────────────────────────┘
                             │ REST / WebSocket
┌────────────────────────────┴────────────────────────────────┐
│                    FastAPI (Python) Backend                  │
│     ┌──────────┬──────────┬──────────┬──────────────┐       │
│     │  Finance │ Materials│Production│  AI Agents   │       │
│     └──────────┴──────────┴──────────┴──────────────┘       │
│              Domain services │ Business rules                │
└──────┬────────────────────────────────────────┬─────────────┘
       │                                        │
┌──────┴───────────────┐              ┌────────┴─────────────┐
│    PostgreSQL        │   ⇄ sync ⇄   │       Qdrant         │
│  (transactional      │              │  (semantic index,    │
│   source of truth)   │              │   embeddings)        │
└──────────────────────┘              └──────────────────────┘
```

### Design principles

1. **AI-first interfaces.** Every API endpoint, schema, error message, and documentation page is designed for agent consumption first. Responses are structured, self-describing, and semantically rich — so agents can reason, decide, and act without human mediation. Human UIs are built *on top of* the agent-facing layer, not the other way around.
2. **Dual-write by default.** Every business document written to PostgreSQL is embedded and indexed in Qdrant on the same transaction boundary. Semantic search is a first-class query pattern, not an afterthought.
3. **Domain-driven modules.** Finance, Materials, and Production each own their tables, services, and APIs. Clean boundaries, clean ownership.
4. **Agents as operators and consultants.** Agents don't just query data — they configure, customise, troubleshoot, and run the system. The role traditionally filled by ERP consultants (functional and technical) is designed for AI agents first, human experts second.
5. **Offline-capable by design.** No mandatory cloud, no mandatory SaaS. Run it on a laptop, a server, or a Kubernetes cluster.

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | **Python 3.12 + FastAPI** | Fast, async, excellent typing, AI-tool-friendly codebase |
| Database | **PostgreSQL 16** | Mature, handles complex relational schemas, rock-solid in production |
| Vector store | **Qdrant** | Purpose-built, scales independently, proven in production |
| Embeddings | **Qwen3-Embedding (local via Ollama)** | Zero-cost, self-hosted, no vendor lock-in |
| Frontend | **React 18 + TypeScript + Vite** | Type-safe, component ecosystem, contributor-friendly |
| Deployment | **Docker + Kubernetes** | Portable, horizontally scalable, industry-standard |
| Observability | **OpenTelemetry + Grafana** | Open standards, self-hosted |

## Roadmap

- **v0.1 — Foundation.** Auth, tenancy, core master data (Company Code, Chart of Accounts, Material Master, Vendor, Customer). PostgreSQL + Qdrant dual-write pipeline.
- **v0.2 — Finance core.** General Ledger, posting engine, document types, fiscal year/period management.
- **v0.3 — Materials Management.** Purchase Requisitions, Purchase Orders, Goods Receipt, Invoice Verification.
- **v0.4 — Production Planning.** BOM, Routing, Work Centers, basic MRP run.
- **v0.5 — AI agents.** Semantic search UI, agentic document understanding, natural-language reporting.
- **v1.0 — Production-ready.** Full transactional coverage of Finance, Materials, and Production, migration tooling, battle-tested at pilot customers.

## Status

**Pre-alpha.** Architecture and foundation in active development. Not yet runnable.

Follow the repository for progress. First public release target: Q3 2026.

## Extending OpenSpine

**You never fork the core.** Every business is different, and every business needs customisation — but forking a monorepo and cherry-picking upstream changes forever is the path to pain. OpenSpine is built from day one to be extended *without touching the core repository*.

Five extension mechanisms, in order of increasing power:

### 1. Configuration (no code)

Most "customisation" in ERPs isn't really code — it's settings. Number ranges, account determination, posting keys, tolerance groups, output types. All of that lives in configuration tables, editable through the UI or YAML. No deployment needed, no developer needed.

### 2. Custom fields (schema extension)

Need a "Customer VAT Classification" field on the customer master? Add it via a migration in your plugin. OpenSpine exposes a custom-field API that lets plugins extend standard entities without altering core tables — the extensions live in plugin-owned columns that travel with the entity through the entire system, including the UI and semantic index.

### 3. Hook points (business logic injection)

Inspired by SAP's BAdIs and enhancement spots, every business transaction emits hooks you can subscribe to:

```python
# In your plugin: my_plugin/hooks.py
from openspine.hooks import hook

@hook("invoice.pre_post")
def validate_custom_tax_code(ctx, invoice):
    if invoice.country == "TR" and not invoice.custom_tax_id:
        raise ValidationError("Turkish tax ID required")

@hook("purchase_order.post_create")
async def notify_quality_team(ctx, po):
    if po.total_value > 100_000:
        await ctx.email.send("quality@acme.com", f"High-value PO {po.id}")
```

Hook points are documented, versioned, and stable. Core releases will not silently break your hooks — breaking changes go through a deprecation cycle.

### 4. Custom endpoints and modules

Need an entire industry module — say, automotive batch tracking or pharmaceutical serialisation? Build it as a plugin. Plugins can expose their own REST endpoints, their own database tables, their own UI pages, and consume the core domain services as a dependency.

```python
# my_plugin/endpoints.py
from openspine.core import router, depends
from openspine.mm import MaterialService

@router.post("/acme/batch-certificate")
def issue_certificate(batch_id: str, mm: MaterialService = depends()):
    material = mm.get_by_batch(batch_id)
    return generate_certificate(material)
```

### 5. UI extensions

The React frontend uses a registry pattern. Plugins can:

- Add new menu items and routes
- Inject custom tabs into standard entity screens (Material Master, Customer, etc.)
- Replace default field renderers with custom components
- Ship their own dashboards

All declared in the plugin's `plugin.yaml` — no fork of the frontend required.

### Anatomy of a plugin

```
acme-openspine-plugin/
├── plugin.yaml              # Manifest: metadata, hook subscriptions, compatibility
├── pyproject.toml           # Standard Python package
├── src/acme_plugin/
│   ├── hooks.py             # Backend hook handlers
│   ├── fields.py            # Custom-field definitions
│   ├── endpoints.py         # Custom REST APIs
│   ├── migrations/          # Schema migrations for plugin-owned tables
│   └── ui/                  # React components, registered via plugin.yaml
└── tests/
```

### Distribution

Plugins are ordinary packages. Ship them privately inside your company, publish them to PyPI, or submit them to the **OpenSpine Plugin Marketplace** (coming with v0.5) for the wider community. A plugin declares its OpenSpine compatibility range — `openspine_compatible: ">=1.0,<2.0"` — so upgrades are predictable.

**The net effect:** your customisations live in your own repository, on your own release cycle, and pulling a new OpenSpine version is as simple as bumping a dependency. No merge hell. Ever.

For the full extension guide, see [ARCHITECTURE.md](./ARCHITECTURE.md).

## Documentation map

- [ARCHITECTURE.md](./ARCHITECTURE.md) — system design and plugin model overview
- [`docs/modules/`](./docs/modules/README.md) — what each Phase 1 module covers (MD, FI, CO, MM, PP)
- [`docs/identity/`](./docs/identity/README.md) — tenancy, users, roles, permissions, authentication
- [`docs/roadmap/`](./docs/roadmap/README.md) — release milestones
- [`docs/decisions/`](./docs/decisions/README.md) — architectural decision records
- [CONTRIBUTING.md](./CONTRIBUTING.md) — how to get involved

## Contributing

Community is what makes this project succeed or fail. If you are:

- An **ERP consultant** (functional or technical) from any platform — SAP, Oracle, Dynamics, NetSuite, Odoo — your domain knowledge is the single most valuable input
- A **developer** interested in enterprise software built right
- A **business** frustrated with the current ERP landscape

…please get in touch. Contribution guide coming soon.

## License

OpenSpine is released under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

This is a deliberate choice. AGPL ensures that any modified version — including SaaS offerings — must also be open-sourced. OpenSpine will stay free, forever, for everyone.

---

*Built by [Beyhan Meyralı](https://www.linkedin.com/in/beyhanmeyrali/) and the OpenSpine community.*
