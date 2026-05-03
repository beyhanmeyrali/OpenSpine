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
- **RBAC + auth-object engine (v0.1 §4.3).** Twelve more `id_*`
  tables: auth-object catalogue (`id_auth_object` + `_action` +
  `_qualifier`), two-tier role model (`id_role_single` +
  `id_role_composite` + `id_role_composite_member`), permission
  linker (`id_permission` with JSONB qualifier values), principal
  bindings (`id_principal_role`), SoD (`id_sod_rule` + `_clause` +
  `_override`), and append-only `id_auth_decision_log`. Migration
  `0003_rbac_core` lands the schema, RLS policies, and BEFORE
  UPDATE triggers.
- **System catalogue.** A seed pack ships in
  `openspine.identity.system_catalogue`: 13 auth objects (system
  + sample MD/FI/MM for SoD targets), 17 system single roles, 4
  composite roles (SYSTEM_TENANT_ADMIN, SYSTEM_AUDIT_READER,
  SYSTEM_AI_OPERATOR, SYSTEM_PLUGIN_ADMIN), 4 SoD baseline rules
  (AP post+pay, BP create+pay, GR+IR three-way-match, token
  issue+audit warn rule). `seed_system_catalogue()` is idempotent
  (keyed on `system_key`); `bootstrap_tenant_and_admin` invokes
  it automatically and grants the admin SYSTEM_TENANT_ADMIN.
  `openspine seed-system-catalogue --tenant-slug <slug>` re-applies
  the catalogue after a system pack update.
- **Authorisation evaluator.** `evaluate(ctx, domain, action,
  qualifier_values)` walks principal → role bindings → composite
  expansion → permissions, intersects each permission's qualifier
  shape with the binding's scope qualifiers, runs SoD-before-allow,
  and writes an `id_auth_decision_log` row. Four qualifier
  matchers: `string_list`, `numeric_range`, `amount_range`,
  `wildcard`. `enforce()` is the raising variant; `@requires_auth(
  domain, action, **extractors)` is the FastAPI decorator. SoD
  block-rule violation overrides any positive grant.
- **Role-assignment HTTP surface.** `POST/DELETE
  /auth/principals/{id}/roles[/{binding_id}]` for binding/unbinding
  single + composite roles, gated by `system.role:assign` and
  `system.role:revoke`. Cross-tenant attempts return 404 (no leak).
- **Audit trace_id propagation.** Every audit row written by
  `write_audit_event` carries `trace_id` for cross-store joins;
  decision-log rows do the same. The middleware extracts the trace
  id from incoming W3C `traceparent` headers when present.
- **Master Data core (v0.1 §4.4).** Twenty-seven `md_*` tables.
  Global catalogues (no tenant_id, no RLS): `md_currency`,
  `md_exchange_rate_type`, `md_uom`, `md_uom_conversion`. The
  remaining 23 tenant-scoped: org structure
  (`md_company_code`, `md_plant`, `md_storage_location`,
  `md_purchasing_org`, `md_purchasing_group`, `md_controlling_area`),
  calendars (`md_factory_calendar`, `md_fiscal_year_variant`,
  `md_posting_period`), CoA + GL master (`md_chart_of_accounts`,
  `md_account_group`, `md_gl_account`, `md_gl_account_company`),
  Business Partner (`md_business_partner`, `md_bp_role`,
  `md_bp_address`, `md_bp_bank`), Material
  (`md_material`, `md_material_plant`, `md_material_valuation`,
  `md_material_uom`), `md_fx_rate`, `md_number_range`.
- **MD global catalogue seeded at bootstrap.** 20 ISO 4217
  currencies, 3 FX rate types (M/B/G), 20 UoMs (EA, KG, M, L, etc.).
  `seed_md_globals()` is idempotent and runs as part of
  `bootstrap_tenant_and_admin`.
- **System auth-object catalogue extended.** Six new MD auth
  objects (`md.company_code`, `md.plant`, `md.chart_of_accounts`,
  `md.fx_rate`, `md.posting_period`, `md.number_range` — joining
  the existing `md.business_partner`, `md.material`,
  `md.gl_account`). Eight MD single roles (one per maintenance
  area). Two new composites: `MD_ADMIN` (full master-data admin)
  and `MD_STEWARD` (day-to-day BP/material maintenance). The
  bootstrap admin gets both `SYSTEM_TENANT_ADMIN` and `MD_ADMIN`.
- **Master Data service layer + HTTP surface.** CRUD service
  functions in `openspine.md.service` and FastAPI routes under
  `/md/*`: currencies, UoMs, fiscal-year-variants, charts-of-accounts,
  gl-accounts, company-codes, plants, business-partners (+ get by
  id), materials (+ plant/valuation extensions), fx-rates,
  posting-periods (create + state-toggle). Every mutating route
  gated via `enforce()`. Tenant-scoped number-range allocation
  uses `SELECT ... FOR UPDATE` for safe concurrency.
- **v0.1 §3 acceptance happy-path test.** End-to-end: bootstrap
  tenant + admin → fiscal-year-variant → CoA → 3 GL accounts →
  Company Code → Plant → vendor BP (with role + address) →
  Material (+ plant + valuation views) → FX rate → posting period
  open → close → reopen. All against the live HTTP surface with
  a real Postgres.
- **Agent surface (v0.1 §4.7) — feature-complete.** The third
  audit-shaped stream lands as `id_agent_decision_trace` (migration
  0005, append-only, the "why did the agent do X?" log per
  `docs/identity/README.md` §"Audit topology"). `POST /agents/traces`
  is the agent-only write endpoint — a human principal calling it
  gets `403 not_an_agent`. The decision trace joins to the
  corresponding `id_audit_event` row via `trace_id`.
- **Hybrid `/md/search` endpoint.** GET /md/search?q=&entity=
  business_partner|material returns ranked candidates with the
  semantic-then-structured contract from `ARCHITECTURE.md` §7.
  v0.1 ships the structured fallback (Postgres ILIKE) while the
  Qdrant index is empty; the response shape includes
  `_meta.source` so agents see one schema across the cold-start
  and warm-cache states.
- **`_meta` self-describing block.** `openspine.agents.meta`
  exposes `build_meta_block`, `meta_for_business_partner`,
  `meta_for_company_code`, `meta_for_search_result`. Wired into
  `/auth/me`, `/md/business-partners` (POST + GET), and
  `/md/company-codes` (POST + list). The block carries `self`,
  `related`, and `actions[{name, method, href, requires}]` so an
  agent can discover the API surface without reading external
  docs. Future endpoints follow the same pattern.
- **v0.1 is feature-complete.** §4.1 bootstrap, §4.2 identity, §4.3
  RBAC + auth-object engine, §4.4 master data, §4.5 event-bus
  skeleton, §4.6 plugin host, §4.7 agent surface, §4.8
  observability — all landed.
- **Real readiness probes.** `/system/readiness` runs concurrent
  per-dep checks (Postgres SELECT 1, Redis PING, Qdrant /readyz,
  Ollama /api/tags) with 2s per-probe timeout. Postgres + Redis
  are required (down → 503). Qdrant + Ollama are optional. 5 unit
  tests + 1 integration test.
- **CI integration lane.** GitHub Actions services bring up
  Postgres 16 + Redis 7 + Qdrant; the job runs alembic migrate
  then `pytest -m integration`. Replaces the example-plugin-only
  job. Documented in `docs/architecture/development.md`.
- **Embedding indexer activated.** `openspine.workers.indexer` is
  the in-process worker subscribed to `master_data.**` on the bus.
  MD service publishes events on BP/material create; the indexer
  embeds the indexable text and upserts into the per-tenant
  Qdrant collection. `openspine.core.qdrant.collection_name(tenant)`
  is the naming convention.
- **`/md/search` rewritten** — Qdrant semantic candidates first,
  verified against Postgres (RLS-scoped). On empty/Qdrant-down
  the existing structured-fallback path kicks in. `_meta.source`
  ∈ {`semantic`, `structured`} so agents see a single contract.
- **`/system/reconcile-embeddings`** — the v0.1 §3 #4 acceptance
  criterion. Re-walks MD entities and re-upserts vectors. Gated by
  `system.tenant:configure`. Drop a Qdrant collection, hit this
  endpoint, search works again.
- **Embeddings: Qwen3-Embedding-0.6B over OpenAI-compatible
  /v1/embeddings.** Replaces the placeholder `qwen2.5:1.5b` (which
  was a chat model and would have errored). Real embedding model:
  1024-d, ~640 MB, MTEB-multilingual 64.33. Works against Ollama
  (`ollama pull qwen3-embedding:0.6b`) OR llama-server
  (`llama-server -m Qwen3-Embedding-0.6B-Q8_0.gguf --embedding`)
  unmodified — same OpenAI shape on both. Adopters who don't want
  Ollama can run llama.cpp directly. Deterministic SHA-512 fallback
  in `embed_text()` keeps CI green when no provider is reachable.
- **161 tests pass** against the live stack (115 unit + 46
  integration). Remaining v0.1 closeouts are the §8 acceptance
  review (4 external reviewers).
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

### Infrastructure

- **Local development stack now end-to-end testable.** `psycopg[binary]`
  added as a sync driver for Alembic; `set_config()` replaces
  `SET LOCAL` (which doesn't accept bind parameters) for the RLS GUC
  in middleware / service / core; integration tests rewritten on
  `httpx.AsyncClient + ASGITransport` so the app, the asyncpg
  engine, and the fixtures share one event loop. pytest config
  pins session-scoped event loops. 22 integration tests now pass
  against a live Postgres + Qdrant stack.

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
