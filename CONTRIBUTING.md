# Contributing to OpenSpine

Thank you for looking. OpenSpine is pre-alpha, community-driven, and deliberately ambitious — if you are here, you are early, and your input shapes the project.

## Who we are looking for

- **ERP consultants** (SAP, Oracle, Dynamics, NetSuite, Odoo, Infor — the platform does not matter). Your domain knowledge is the single most valuable input to this project. Help us get the data models, business processes, and customisation surfaces right.
- **Backend developers** comfortable with Python, FastAPI, PostgreSQL, event-driven architectures.
- **Frontend developers** who know React + TypeScript and care about building interfaces that are equally usable by humans and agents.
- **AI / agent developers** interested in making agents first-class ERP users.
- **Businesses** with real pain — tell us what hurts in your current ERP.

## How to start

1. **Read the docs.** Start with [README.md](./README.md) and [ARCHITECTURE.md](./ARCHITECTURE.md), then browse [`docs/`](./docs/). For the current development plan, see [`docs/roadmap/v0.1-foundation.md`](./docs/roadmap/v0.1-foundation.md).
2. **Open an issue before large work.** For anything beyond a typo fix or clarification, open a GitHub issue describing the change you want to make. This avoids wasted effort and gets the design validated first.
3. **Keep PRs small and focused.** One concern per PR. Document the *why* in the PR description.
4. **Set up locally.** See [`docs/architecture/development.md`](./docs/architecture/development.md) for the full setup. Short version: `make dev` (creates venv, installs deps, installs pre-commit), then `make up` (starts Postgres / Redis / Qdrant / Ollama via Docker Compose).
5. **Run the CI gauntlet locally before pushing.** CI runs ruff lint, ruff format check, mypy, and pytest. `make check` runs all four. Pushing without it wastes CI minutes and reviewer time.

## Council pattern (advanced)

For cross-module work, OpenSpine ships a council of project-scoped Claude Code subagents under `.claude/agents/` (`md-expert`, `fico-expert`, `mm-expert`, `pp-expert`, `identity-expert`, `ai-agent-architect`, `plugin-architect`, `solution-architect`). The orchestrator instructions in [`CLAUDE.md`](./CLAUDE.md) route prompts to the right expert and convene a council for cross-module concerns. Use it when designing changes that span modules — the experts encode the doc-grounded knowledge for their domain.

## Contribution licensing

- OpenSpine is licensed under **AGPL-3.0**.
- By submitting a pull request, you agree that your contribution is released under the same license.
- We use the **Developer Certificate of Origin** ([DCO](https://developercertificate.org/)). Every commit must be signed off:
  ```
  git commit -s -m "your message"
  ```
  That appends a `Signed-off-by: Your Name <your@email>` trailer, which certifies you wrote the code and have the right to contribute it.

## Code of conduct

Treat every contributor with respect. No harassment, no discrimination, no hostility. Disagreements are welcome; rudeness is not.

A full Code of Conduct will be added before v0.1. In the meantime: behave like the colleague you would want to work with.

## Communication

- **GitHub Issues** — for bugs, design discussions, and feature requests.
- **GitHub Discussions** — for open-ended questions and ideas (enabled soon).
- **LinkedIn** — [Beyhan Meyralı](https://www.linkedin.com/in/beyhanmeyrali/) is the most reliable direct contact during pre-alpha.

## What you should NOT do

- Do not submit pull requests that add dependencies on proprietary or non-AGPL-compatible libraries.
- Do not copy-paste code from commercial ERP systems. If your contribution reflects general industry practice, that is fine; if it reflects a specific vendor's proprietary implementation, it is not.
- Do not open PRs against the core to add customer-specific logic. Ship it as a plugin — that is precisely what the plugin system exists for.

---

*This document will grow as the project matures. Until then, ask if something is unclear.*
