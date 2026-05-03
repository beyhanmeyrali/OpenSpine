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
- **Identity core (v0.1 §4.2).** Ten `id_*` tables with full RLS:
  `id_tenant` (global registry, no RLS), `id_tenant_setting`,
  `id_principal` (single table for human/agent/technical),
  `id_human_profile`, `id_agent_profile`, `id_credential`,
  `id_session`, `id_token` (single table; `kind` discriminates;
  agent-token CHECK enforces expires_at + provisioner + reason at
  the database level), `id_federated_identity` (stub for v0.2 SSO),
  `id_audit_event` (append-only). One reusable
  `_id_touch_updated_audit()` plpgsql trigger function. Bootstrap
  cycle resolved via `DEFERRABLE INITIALLY DEFERRED` FKs on the
  audit-author and tenant-id columns. Full schema-invariants test
  (`tests/test_schema_invariants.py`) catches drift on tenant-id,
  audit columns, FK indexes, VARCHAR types, and table prefixes.
- **Identity security primitives.** Argon2id passwords (with
  forward-compat parameter-tuning via `check_needs_rehash`); pyotp
  TOTP (RFC 6238 defaults); opaque 256-bit tokens with SHA-256
  storage and constant-time verification. The shift to SHA-256 for
  tokens (from argon2id in `authentication.md` v0) is documented
  inline with rationale: argon2 defends low-entropy secrets;
  256-bit cryptographic randoms gain nothing from it and pay ~50ms
  per request.
- **Auth surface.** `POST /auth/login` (password + optional TOTP),
  `POST /auth/logout`, `GET /auth/me`, `POST /auth/tokens` (issue
  user_api / agent / service tokens), `DELETE /auth/tokens/{id}`,
  `POST /auth/totp/enrol`, `POST /auth/totp/verify`. The login
  envelope is identical for unknown-tenant, unknown-user, and
  wrong-password cases — no enumeration leak. Wrong-password path
  re-hashes if argon2 parameters drifted.
- **Principal-context middleware.** Per-request identity resolution
  (bearer token first, then session cookie, then anonymous), W3C
  trace-context propagation, `SET LOCAL openspine.tenant_id` for
  RLS. Anonymous fast-path skips the DB transaction.
- **Bootstrap CLI.** `openspine create-tenant --name --slug
  --admin-email [...]` atomically seeds the first tenant + admin +
  password credential. Runs offline against the database — no
  privileged anonymous HTTP path needed. Honours
  `OPENSPINE_BOOTSTRAP_ADMIN_PASSWORD` env var; auto-generates and
  prints once otherwise.
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
- **Token storage hash** in `docs/identity/authentication.md` —
  argon2id replaced with SHA-256 for token plaintexts (argon2 only
  for low-entropy passwords; rationale in
  `src/openspine/identity/security.py`).
- **Audit-author FK indexing** in `docs/architecture/data-model.md`
  — clarified that `created_by`/`updated_by` are exempt from the
  "every FK gets an index" rule (write-amplifying on every business
  write, rare on routine reads). Schema-invariants test exempts
  these columns.

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

[Unreleased]: https://github.com/beyhanmeyrali/openspine/compare/main...claude/review-codebase-8XRRf
