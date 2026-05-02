# Changelog

All notable changes to OpenSpine are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file tracks change at the level of "what an adopter or contributor
would care about". Implementation-detail commits are visible in `git log`.

## [Unreleased]

The work below is on `claude/review-codebase-8XRRf` toward `v0.1`. It
will fold into `0.1.0` when v0.1 ships per `docs/roadmap/v0.1-foundation.md`.

### Added

- **Project foundation.** `pyproject.toml`, `src/openspine/` package layout
  per module (core, identity, md, fi, co, mm, pp, workers), `Makefile`,
  `docker-compose.yml` (Postgres 16, Redis 7, Qdrant, Ollama),
  multi-stage `Dockerfile`, `.env.example`, `.editorconfig`,
  `.pre-commit-config.yaml`, GitHub Actions CI (lint, typecheck, test,
  example-plugin-integration, build-image), Alembic root revision.
- **FastAPI app skeleton.** Health, readiness, hooks, plugins, metrics
  endpoints. Structured error envelope per `docs/identity/permissions.md`.
- **Observability.** OpenTelemetry tracing (OTLP gRPC); Prometheus
  metrics with eight domain-shaped counters/histograms (HTTP, hooks,
  events published/consumed, embedding indexed, embedding lag, auth
  decisions); request-timing middleware.
- **Event bus.** `Event` envelope; `EventBus` Protocol with
  `InMemoryEventBus` test double; glob pattern matcher (`*` for one
  segment, `**` for one-or-more); embedding-worker entrypoint
  subscribed to `master_data.**`.
- **Plugin host (v0.1 §4.6).** Discovery via Python entry points,
  pydantic-validated `PluginManifest` (strict, extra=forbid), PEP 440
  compatibility check (mandatory range), three-state lifecycle
  (`loaded` / `skipped_incompatible` / `failed`), route mounting,
  `/system/plugins` endpoint. Reference plugin in
  `examples/openspine-plugin-example/`.
- **Council subagents.** Eight project-scoped Claude Code subagents
  (`md-expert`, `fico-expert`, `mm-expert`, `pp-expert`,
  `identity-expert`, `ai-agent-architect`, `plugin-architect`,
  `solution-architect`) with `CLAUDE.md` orchestrator routing.
- **Documentation.** `docs/roadmap/v0.1-foundation.md` (development
  plan); `docs/architecture/data-model.md` (schema conventions);
  `docs/architecture/event-catalogue.md`; `docs/architecture/plugin-system.md`;
  `docs/architecture/development.md` (dev setup); audit-topology section in
  `docs/identity/README.md`.

### Changed

- **README dual-write diagram** changed `⇄ sync ⇄` to `→ async index →`
  with `authoritative` / `rebuildable derivative` labels to match the
  actual eventual-consistency story.
- **README tech-stack table** added Redis Streams.
- **Module dependency graph** in `docs/modules/README.md` arrow direction
  inverted to match `ARCHITECTURE.md` §5 convention (`A --> B` =
  "A calls / depends on B"), with an explicit convention note.
- **FX rate reference** in `docs/identity/permissions.md` —
  authorisation amount conversions explicitly use rate type `M`
  (average), never `B` or `G`.
- **FI hook names** renamed `fi_document.*` → `journal_entry.*` per
  ADR 0008.

### Decisions (ADRs)

- [`0001`](docs/decisions/0001-postgres-as-source-of-truth.md) —
  PostgreSQL is the single source of truth.
- [`0002`](docs/decisions/0002-qdrant-over-pgvector.md) — Qdrant for the
  semantic index; collection-per-tenant **forever**.
- [`0003`](docs/decisions/0003-universal-journal.md) — FI and CO share
  `fin_document_*` (the universal journal, ACDOCA-style).
- [`0005`](docs/decisions/0005-tenant-isolation-model.md) — Tenant
  isolation is logical only (shared schema + RLS + per-tenant Qdrant
  collection); database-per-tenant rejected forever; default deployment
  is single-tenant per installation.
- [`0008`](docs/decisions/0008-hook-naming-convention.md) — Hook names
  are bare `entity.{pre,post}_{verb}` (no module prefix);
  `journal_entry` replaces `fi_document` for FI hooks.

### Pending

The following are deliberately deferred per owner direction:

- **ADR 0004** — AGPL + plugin-distribution legal nuance. Held for
  legal counsel involvement.
- **§4.2 identity-core schema** — Strategic decisions go through
  council (`identity-expert` + `ai-agent-architect` + `solution-architect`)
  before implementation.

[Unreleased]: https://github.com/beyhanmeyrali/openspine/compare/main...claude/review-codebase-8XRRf
