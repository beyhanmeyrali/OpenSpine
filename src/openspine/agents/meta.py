"""`_meta` self-describing block helper.

Per `ARCHITECTURE.md` §10 #1 + §7 (agent integration pattern),
every response should let an agent discover the API by interacting
with it. The `_meta` block carries:

- `self`: the canonical href for this resource
- `related`: hrefs for related resources (parent, children,
  same-entity siblings)
- `actions`: machine-readable list of `{name, method, href, requires}`
  for actions the caller could take next; `requires` lists the
  `(domain, action)` permissions an agent needs to attempt the
  action (so the agent can decide whether to even try).

The contract is intentionally simple — full HATEOAS would be
nice but harder to keep in sync. v0.1 ships the contract on a
representative set of endpoints; v0.2 expands.
"""

from __future__ import annotations

import uuid
from typing import Any


def build_meta_block(
    *,
    self_href: str,
    related: dict[str, str] | None = None,
    actions: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a `_meta` dict ready to splice into a response payload.

    `extra` is merged at the top level — useful for endpoint-specific
    hints (e.g., `{"notes": "results capped at 10"}`).
    """
    block: dict[str, Any] = {"self": self_href}
    if related:
        block["related"] = related
    if actions:
        block["actions"] = actions
    if extra:
        block.update(extra)
    return block


def meta_for_business_partner(bp_id: uuid.UUID) -> dict[str, Any]:
    return build_meta_block(
        self_href=f"/md/business-partners/{bp_id}",
        related={
            "addresses": f"/md/business-partners/{bp_id}/addresses",
            "banks": f"/md/business-partners/{bp_id}/banks",
            "roles": f"/md/business-partners/{bp_id}/roles",
        },
        actions=[
            {
                "name": "change",
                "method": "PATCH",
                "href": f"/md/business-partners/{bp_id}",
                "requires": [["md.business_partner", "change"]],
                "available_in": "v0.2",
            },
        ],
    )


def meta_for_company_code(cc_id: uuid.UUID) -> dict[str, Any]:
    return build_meta_block(
        self_href=f"/md/company-codes/{cc_id}",
        related={
            "plants": f"/md/plants?company_code_id={cc_id}",
            "posting_periods": (f"/md/company-codes/{cc_id}/posting-periods"),
        },
    )


def meta_for_search_result(
    *,
    query: str,
    entity: str,
    source: str,
    total: int,
) -> dict[str, Any]:
    """Build the `_meta` block returned by `/md/search`.

    `source` ∈ {`semantic`, `structured`, `hybrid`} — the agent uses
    it to know whether to trust the ranking (semantic = approximate)
    or treat it as exact (structured fallback).
    """
    return build_meta_block(
        self_href=f"/md/search?q={query}&entity={entity}",
        extra={
            "query": query,
            "entity": entity,
            "source": source,
            "total": total,
            "pattern": "semantic-then-structured",
            "notes": (
                "Per ARCHITECTURE.md §7: candidates ranked by Qdrant "
                "(or Postgres ILIKE fallback when the semantic index "
                "is empty). Always verify against the structured row "
                "before acting."
            ),
        },
    )


__all__ = [
    "build_meta_block",
    "meta_for_business_partner",
    "meta_for_company_code",
    "meta_for_search_result",
]
