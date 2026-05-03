# HANDOFF — overnight session notes

This is a working note from an overnight Claude Code session. It documents
state-as-of-last-commit so a fresh session (or a human reviewer) can
continue without context loss. Safe to delete once you've read it.

## Cron-trigger status

The `/loop 1h` request couldn't be honoured literally. This Claude Code
environment does not expose `CronCreate` or `ScheduleWakeup`, so there is
no way to auto-fire the resume prompt on an hourly cadence. The session
that received the request continued building in-line instead.

If you want true scheduled execution, the options are:

- Run a fresh session and paste the resume prompt below.
- Use a system-level cron (outside Claude Code) that invokes the Claude
  Code CLI with the resume prompt as input.
- Check whether your environment supports the Claude Code automation hooks
  that wrap `cron`-style triggers; if so, configure them with the resume
  prompt.

## Resume prompt (paste into a fresh session)

```
You are the owner of OpenSpine, currently developing on branch
claude/review-codebase-8XRRf. Read the following before any action:

1. docs/roadmap/v0.1-foundation.md (the development plan)
2. CLAUDE.md (orchestrator routing, non-negotiables, conventions)
3. HANDOFF.md (state of last session)
4. git log --oneline (most recent commits show progress)

Then identify the next work-stream from v0.1-foundation.md §4 that
hasn't been started. Make focused incremental progress on ONE work-stream
at a time. After each change:
- run `pytest -m "not integration"` (must pass)
- run `ruff check src tests` (must pass)
- commit with a descriptive message ending in
  "https://claude.ai/code/session_<id>"
- push to origin claude/review-codebase-8XRRf

Hard rules:
- Do NOT make hook-naming reconciliation decisions. The plan §6 marks
  this as a v0.1 blocker requiring council input.
- Do NOT draft ADR 0004 (AGPL plugin distribution). Legal nuance needs
  human judgement.
- Do NOT force-push or rewrite history.
- If a step needs human judgement that the plan flags as needing
  council/owner input, append a section to NEEDS-INPUT.md at repo root
  with a clear question and stop that step. Move to the next.
- Stay strictly inside the v0.1 scope. PRs adding fin_*, mm_*, pp_*
  rows wait for v0.2/v0.3/v0.4.
```

## Hard limits the session is honouring

- No edits to ARCHITECTURE.md, README.md sections that the human owner
  signed off on, the LICENSE, or the existing module spec docs without a
  clear small-fix justification.
- No new dependencies in `pyproject.toml` without recording them here
  with reason.
- Every commit is a single focused change with a real message. No
  "wip" commits, no force-pushes, no amends.
- Tests run after every code change. The smoke suite must stay green.

## Work-stream ledger (v0.1-foundation.md §4)

Update this list as work-streams move through their states.

| § | Stream | State | Notes |
|---|--------|-------|-------|
| 4.1 | Bootstrap | DONE | Commit `e7d9439`. 14 tests pass. |
| 4.2 | Identity core | DONE | Ten `id_*` tables; one shared trigger function; RLS on every tenant-scoped table. Single-table `id_token` discriminated by `kind`; agent-token CHECK invariants enforced at the database. Argon2id passwords + pyotp TOTP + SHA-256-stored opaque tokens. `/auth/{login,logout,me,tokens,totp/{enrol,verify}}` HTTP surface. Principal-context middleware + RLS GUC plumbing. `openspine create-tenant` bootstrap CLI. Schema-invariants test runs against ORM metadata. 115 unit tests + 7 integration tests pass. Pending pieces (RBAC, auth-object engine, decision log) land in §4.3 as planned. |
| 4.3 | RBAC + auth-object engine | DONE | 12 RBAC tables (auth_object/_action/_qualifier, role_single/_composite/_member, permission, principal_role, sod_rule/_clause/_override, auth_decision_log). System catalogue seed (13 auth objects, 17 single roles, 4 composite roles, 4 SoD rules) loaded automatically by bootstrap_tenant_and_admin and the new `openspine seed-system-catalogue` CLI subcommand. Auth evaluator + `@requires_auth(domain, action, **extractors)` decorator with composite-role expansion, four qualifier matchers (string_list, numeric_range, amount_range, wildcard), SoD-before-allow, decision-log writes. /auth/principals/{id}/roles assign + revoke endpoints gated by ROLE_ASSIGN. 22 RBAC integration tests pass; 141/141 total. |
| 4.4 | Master Data core | DONE | 27 md_* tables (4 globals + 23 tenant-scoped) covering org structure, calendars, CoA + GL, BP, Material, FX, posting periods, number ranges. Global currency/UoM/rate-type catalogues seeded automatically by bootstrap. System catalogue extended with 6 md.* auth objects, 8 MD single roles, MD_ADMIN + MD_STEWARD composite roles. Service layer + HTTP routes for the v0.1 happy-path entities. /md/{currencies, uoms, fiscal-year-variants, charts-of-accounts, gl-accounts, company-codes, plants, business-partners, materials, material-plants, material-valuations, fx-rates, posting-periods} all gated by enforce(). v0.1 §3 acceptance happy-path test passes end-to-end. 144/144 tests. |
| 4.5 | Event bus + embedding pipeline | SKELETON DONE | Event envelope + EventBus protocol + InMemoryEventBus + glob pattern matcher (`*`/`**`); per-tenant Qdrant collection naming convention (`openspine__<tenant>`); embedding worker entrypoint subscribed to `master_data.**`. Real Redis/Qdrant clients deferred until integration tests can run. 41 tests pass. |
| 4.6 | Plugin host | DONE (subject to §4.3/§4.4 wiring) | Discovery via Python entry points; pydantic-validated PluginManifest; PEP 440 compatibility check; three-state lifecycle; per-plugin record. Routes from manifest mounted on FastAPI app at startup. `/system/plugins` reports state. Example plugin in examples/openspine-plugin-example/. CI integration job installs core + example, runs `pytest -m integration` to verify the full discovery → manifest → hook → route pipeline. Auth-object registration and custom-field column generation are accepted in the manifest now and activate when §4.3 / §4.4 land. 67 tests pass. |
| 4.7 | Agent surface | PARTIAL | Structured-error envelope done (core/errors.py + main.py exception handler — denial semantics ready for agent self-correction). Remaining pieces (`_meta` block on responses, agent-token shape, agent-decision-trace, hybrid /md/search) wait on §4.2 and §4.4. |
| 4.8 | Observability | DONE | OTel tracing scaffolded with FastAPI auto-instrumentation; Prometheus `/metrics` endpoint; `MetricsMiddleware` records request count + latency by (method, route, status); domain-shaped counters/histograms (events, embedding lag, auth decisions, hook dispatch) registered. 18 tests pass. |

## Open questions accumulating for owner review

These are flagged in the plan's open-questions list and surfacing as I
work. Resolve at your convenience; nothing is blocked-blocked, just
queued at PENDING-OWNER-INPUT for the streams that need them.

1. **Database-per-tenant vs shared+RLS** (`tenancy.md` Q1). The plan
   defaults to shared+RLS for v0.1; confirming or revising this is a
   prerequisite for §4.2's migration design.
2. **Hook-naming canonical form** (`docs/README.md:31` says
   `entity.action`; FI doc uses `fi_document.*`). Needed before §4.6
   plugin host hardens. Best resolved by a council session (`fico-expert`
   + `plugin-architect` + `solution-architect`).
3. **AGPL + private-plugin distribution.** Material legal nuance. Won't
   draft ADR 0004 unattended. Owner attention required.
4. **Qdrant collection topology** (per-tenant collections vs
   shared+payload-filter). v0.1 default is per-tenant; revisit if
   tenant cardinality grows.
5. **FX rate `mid` reference** — `permissions.md:73` mentions "current
   mid-rate" but `md_exchange_rate_type` defines `M`/`B`/`G`. Tiny doc
   fix; deferred until you confirm `M` (average) is the intended ref
   rate or you want a `mid` rate type added.
6. **Audit-log topology** — `id_audit_event` vs `id_auth_decision_log`
   vs `id_agent_decision_trace` split is sensible but undocumented.
   Will write the missing section in `docs/identity/README.md` if the
   stream gets to it; otherwise queued.

## What was done since this branch started

Commits on `claude/review-codebase-8XRRf`, oldest first:

- Add domain-expert subagents and orchestrator routing
- Add v0.1 development plan and three foundational ADRs
- Apply uncontroversial doc-review fixes
- Bootstrap the Python project (v0.1 §4.1)
- Add HANDOFF.md for overnight session continuity
- Wire OpenTelemetry tracing and Prometheus metrics (v0.1 §4.8)
- Event bus contract + embedding worker skeleton (v0.1 §4.5)
- Add Dockerfile and event catalogue
- Add data-model and plugin-system architecture deep-dives
- Surface open questions in NEEDS-INPUT.md
- Resolve hook-naming convention (ADR 0008)
- Lock tenant isolation to logical only (ADR 0005)
- Resolve audit topology + FX rate reference + clean up NEEDS-INPUT
- Fix CI failures: ruff lint + format + mypy
- Update HANDOFF verification cheat-sheet to match CI
- Plugin host skeleton + reference example (v0.1 §4.6)
- Mount plugin-declared routes on app startup
- Add CI integration job that proves the example plugin works end-to-end
- Add CHANGELOG.md, refresh CONTRIBUTING, add example-plugin tests
- SQLAlchemy declarative base + audit/tenant mixins
- (council-deferred §4.2 work resumed 2026-05-03)
- Identity ORM models + deferrable FKs (v0.1 §4.2 part 1)
- 0002 identity-core migration + schema-invariants test
- Identity security primitives (passwords, TOTP, opaque tokens)
- Audit-event writer + principal-context middleware
- Auth router + service layer + integration tests
- Bootstrap management CLI: openspine create-tenant
- (§4.3 work resumed 2026-05-03)
- RBAC schema + integration-test infra fixes (v0.1 §4.3 part 1)
- System catalogue + seeder + bootstrap auto-seed (v0.1 §4.3 part 2)
- Auth evaluator + @requires_auth + SoD enforcement (v0.1 §4.3 part 3)
- Role assignment endpoints + ROLE_ASSIGN gate (v0.1 §4.3 part 4)
- (§4.4 work resumed 2026-05-03)
- MD ORM models + migration 0004 (v0.1 §4.4 part 1)
- MD global catalogue seed + system auth-object extensions (v0.1 §4.4 part 2)
- MD service layer + HTTP routes + happy-path test (v0.1 §4.4 part 3)
- (further commits land below this line)

## Open questions queued

See `NEEDS-INPUT.md`. Most items resolved in the morning council pass:

- Hook naming → ADR 0008
- Audit topology → identity/README.md section written
- Tenant isolation (DB-per-tenant + Qdrant scaling) → ADR 0005
- FX rate `M` reference → permissions.md updated

Still open by deliberate deferral:

- AGPL plugin distribution (owner deferred until adopter asks)

The remaining open items in `tenancy.md` are post-v1.0.

## Verification cheat-sheet

Run the full CI gauntlet locally before any code-touching push. Pytest
alone is not enough — ruff lint, ruff format check, and mypy all run in
CI and any of them can fail the job:

```bash
ruff check src tests             # lint
ruff format --check src tests    # format
PYTHONPATH=src mypy src          # types
PYTHONPATH=src pytest tests/ -q  # tests
git log --oneline -20            # recent commits
```

`make check` runs lint + typecheck + test once the project venv is
installed via `make dev`.
