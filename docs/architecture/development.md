# Development setup

Pre-alpha. The repo is now bootstrapped to be runnable; the actual business
logic lands milestone by milestone per `docs/roadmap/v0.1-foundation.md`.

## Prerequisites

- Python 3.12
- Docker + Docker Compose v2
- `make`

## First-time setup

```bash
make dev          # creates .venv, installs runtime + dev deps, installs pre-commit
cp .env.example .env
make up           # starts Postgres, Redis, Qdrant, Ollama
docker exec openspine-ollama ollama pull qwen2.5:1.5b   # pull the embedding model
make migrate      # runs Alembic to head (currently a no-op base revision)
make run          # starts the FastAPI app on :8000 with auto-reload
```

Visit:

- http://localhost:8000/system/health — liveness
- http://localhost:8000/docs — OpenAPI / Swagger UI
- http://localhost:8000/system/hooks — registered plugin hooks

## Day-to-day

```bash
make check        # lint + typecheck + test (the same commands CI runs)
make fix          # ruff --fix + format
make test         # pytest with verbose output
make revision MSG="describe the change"   # new Alembic migration
make reset-db     # drop + recreate db + run migrations
```

## Layout

```
.
├── pyproject.toml          # Python package + tool config
├── docker-compose.yml      # Local stack: Postgres, Redis, Qdrant, Ollama
├── Makefile                # All dev commands
├── alembic.ini             # Alembic config (overridden by env.py)
├── migrations/             # DB migrations, one per landed change
├── src/openspine/
│   ├── core/               # Cross-cutting infra (errors, hooks, logging)
│   ├── identity/           # id_* — auth, RBAC, SoD, audit
│   ├── md/                 # md_* — master data
│   ├── fi/                 # fin_* — financial accounting (v0.2)
│   ├── co/                 # co_* — controlling (v0.2.x)
│   ├── mm/                 # mm_* — materials management (v0.3)
│   ├── pp/                 # pp_* — production planning (v0.4)
│   └── workers/            # Embedding worker, reconciliation jobs
├── tests/                  # Pytest tests
├── .claude/agents/         # Project-scoped Claude Code subagents (council)
└── docs/                   # Specifications, ADRs, roadmap
```

## Conventions

- **No JOINs across module table prefixes.** Cross-module data goes through
  service calls.
- **Hooks** follow `entity.{pre,post}_{verb}` per `docs/README.md:31`. (See the
  v0.1 plan §6 — there is one inherited inconsistency to resolve before
  hardening the plugin host.)
- **Tables** are snake_case, prefixed by module owner.
- **Python class names** are PascalCase; modules are snake_case.
- **DCO sign-off** on every commit (`git commit -s`) per `CONTRIBUTING.md`.

## Running the test suite

`make test` runs unit + smoke tests that need no infrastructure. Integration
tests are marked with `@pytest.mark.integration` and skipped unless explicitly
selected. To run them locally:

```bash
make up               # ensure infra is up
pytest -m integration
```

CI runs only the no-infrastructure suite for speed; integration tests run on
release-candidate builds.

## What's currently real

As of the bootstrap commit (v0.1.0.dev0):

- FastAPI app construct + health/readiness/hooks endpoints
- Structured error envelope (per `permissions.md` denial semantics)
- Hook registry + dispatcher (no plugins loaded yet)
- Alembic migration chain rooted at `0001_initial`
- Docker Compose stack for local dev
- Ruff + mypy + pytest + pre-commit + GitHub Actions CI

What's deliberately not yet real:

- Identity tables and auth flows (v0.1 §4.2)
- RBAC + auth-object engine (v0.1 §4.3)
- Master Data tables and services (v0.1 §4.4)
- Event bus producer + embedding worker (v0.1 §4.5)
- Plugin host (v0.1 §4.6)
- Agent surface (v0.1 §4.7)
- OpenTelemetry + Prometheus wiring (v0.1 §4.8)

Each of those has a dedicated work-stream in the v0.1 plan.
