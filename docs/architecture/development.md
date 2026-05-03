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
docker exec openspine-ollama ollama pull qwen3-embedding:0.6b   # pull the embedding model
make migrate      # runs Alembic to head
make run          # starts the FastAPI app on :8000 with auto-reload
```

The default embedding model is **Qwen3-Embedding-0.6B** (1024-d,
~640 MB on disk, MTEB-multilingual 64.33). The indexer talks to the
**OpenAI-compatible `/v1/embeddings` endpoint**, so any of these
servers work without code changes:

- **Ollama** (default; `OPENSPINE_OLLAMA_URL=http://localhost:11434`,
  pull with `ollama pull qwen3-embedding:0.6b`).
- **llama.cpp** (`llama-server -m Qwen3-Embedding-0.6B-Q8_0.gguf
  --embedding`); point `OPENSPINE_OLLAMA_URL` at `http://localhost:8080`.
- Any other server that speaks the OpenAI embeddings shape.

When the embedding service is unreachable, the indexer falls back
to a deterministic SHA-512 pseudo-embedding so CI and the test
suite can exercise the full pipeline without the model. The
fallback is exact-text-equality only (no semantic similarity); it
exists for development affordance, not production.

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

## Running tests

There are two test classes:

- **Unit tests** (default) — no infrastructure required. ORM
  metadata, pure-function security primitives, request validation
  via FastAPI TestClient.
  ```bash
  pytest -m "not integration"
  ```
- **Integration tests** — require the local stack
  (`make up && make migrate`). They exercise the full HTTP surface
  against a live Postgres, real Redis, real Qdrant, and the
  identity / RBAC / MD / agent flows end-to-end.
  ```bash
  make up && make migrate
  pytest -m integration
  ```

CI runs both classes in separate jobs. The integration job
brings up Postgres + Redis + Qdrant as GitHub Actions services
and runs `pytest -m integration`. Ollama isn't started in CI —
embedding-pipeline tests skip when it's unreachable per the
optional-dep contract in `core/readiness.py`.

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
