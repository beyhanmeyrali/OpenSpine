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
| 4.2 | Identity core | PENDING-OWNER-INPUT | Schema design depends on the database-per-tenant vs shared+RLS open question (`tenancy.md` Q1). Won't proceed unattended. |
| 4.3 | RBAC + auth-object engine | BLOCKED-ON-4.2 | Needs identity tables first. |
| 4.4 | Master Data core | BLOCKED-ON-4.2 | Needs `tenant_id` and RLS in place first. |
| 4.5 | Event bus + embedding pipeline | SKELETON DONE | Event envelope + EventBus protocol + InMemoryEventBus + glob pattern matcher (`*`/`**`); per-tenant Qdrant collection naming convention (`openspine__<tenant>`); embedding worker entrypoint subscribed to `master_data.**`. Real Redis/Qdrant clients deferred until integration tests can run. 41 tests pass. |
| 4.6 | Plugin host | BLOCKED-ON-DECISION | Hook-naming reconciliation must be resolved before hardening. |
| 4.7 | Agent surface | BLOCKED-ON-4.2/4.4/4.5 | |
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
- (further commits land below this line)

## Verification cheat-sheet

```bash
git log --oneline -20
PYTHONPATH=src python3 -m pytest tests/ -v
ruff check src tests        # if ruff is installed
mypy src                    # if mypy is installed
```
