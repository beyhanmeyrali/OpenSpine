"""Qdrant client conventions (v0.1 §4.5).

Per ADR 0002 the multi-tenant collection topology is **collection-per-tenant**
in v0.1. This module owns:

- The collection-naming convention (`openspine__<tenant_id>`).
- A thin wrapper that provides `ensure_collection` / `upsert` / `search`.

The actual Qdrant client connection is established lazily so import-time
failure (e.g. Qdrant down in dev) doesn't crash the process. Domain code
calls `qdrant_client()` and gets either a working client or a clear error.

The embedding worker is the only component that *writes* to Qdrant. The
hybrid-search endpoint is the only component that *reads*. Domain services
do not talk to Qdrant directly.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def collection_name(tenant_id: str) -> str:
    """Per-tenant collection naming convention.

    Lowercase, double-underscore-separated, prefixed by the project name so
    a shared Qdrant cluster could host multiple OpenSpine deployments without
    collision.
    """
    return f"openspine__{tenant_id.lower()}"


def parse_tenant_from_collection(name: str) -> str | None:
    """Inverse of `collection_name`. Returns None if the name doesn't match."""
    prefix = "openspine__"
    if not name.startswith(prefix):
        return None
    return name[len(prefix) :]
