# Architecture — Deep dives

The top-level [ARCHITECTURE.md](../../ARCHITECTURE.md) is the authoritative architectural overview. This directory hosts deeper treatments of specific topics as they are written.

## Planned documents

| Doc | Covers |
|-----|--------|
| `data-model.md` | Full schema conventions, cross-module entity relationships, naming rules, indexing strategy, migration approach |
| `event-catalogue.md` | Every published event across modules — name, payload schema, producers, canonical consumers, retention |
| `plugin-system.md` | Deep dive into plugin lifecycle, registration, isolation, upgrade paths |
| `ai-agent-integration.md` | Agent runtime, decision logging, tool interfaces, semantic + structured grounding pattern |
| `security-and-audit.md` | Defence-in-depth model, audit log structure, retention, SOX/GoBD/SAF-T alignment |
| `deployment.md` | Kubernetes reference architecture, Docker Compose reference, observability wiring |
| `performance.md` | Benchmark targets, hot-path budgets, cache strategy |

## Status

All documents in this directory will be added as the system matures. For now, the top-level `ARCHITECTURE.md` is complete enough to inform the module docs in `../modules/` and the identity docs in `../identity/`.
