"""Agent surface — the v0.1 §4.7 cross-cutting affordances.

Three concerns live here:

1. **Agent decision traces** — the "why" stream. Agents post their
   reasoning per action via `POST /agents/traces`. Storage is the
   append-only `id_agent_decision_trace` table from §4.3.

2. **Self-describing responses** — the `_meta` block helper that
   endpoints attach to outbound payloads so an agent can discover
   related resources and available next actions without reading
   external docs.

3. **Hybrid search** — the `/md/search` endpoint (lives in
   `openspine.md.router`) follows the semantic-then-structured
   pattern: Qdrant for candidates, Postgres for verification.
   The `meta_for_search_result` helper here builds that response.

Per `ARCHITECTURE.md` §10 #1, the agent surface is non-negotiable
from v0.1: every API is designed for agents first, humans second.
"""

from openspine.agents.meta import build_meta_block, meta_for_search_result

__all__ = ["build_meta_block", "meta_for_search_result"]
