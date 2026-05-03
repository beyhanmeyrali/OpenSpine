"""Embedding indexer — the worker side of the dual-write pipeline.

The indexer takes a domain entity, embeds its semantic-relevant text,
and upserts the vector into the per-tenant Qdrant collection. The
hybrid `/md/search` endpoint reads back from the same collection.

v0.1 design choices:

1. **In-process by default.** The FastAPI lifespan registers
   `register_indexer()` against the in-memory event bus. Domain
   services publish `master_data.{entity}.created|updated` events
   on commit; the indexer handler runs in-process and upserts.
   This is single-process and synchronous-after-publish — fine for
   the v0.1 demo and the integration tests, swap-in for Redis
   Streams + a separate worker process is a v0.2 concern when scale
   actually demands it.

2. **Provider-agnostic embedding endpoint.** The indexer talks to
   any service exposing the OpenAI-compatible `/v1/embeddings`
   shape:
   - **Ollama** (`ollama pull qwen3-embedding:0.6b`) on :11434.
   - **llama-server** (`llama-server -m Qwen3-Embedding-0.6B-Q8_0
     .gguf --embedding`) on :8080.
   Both use the same llama.cpp kernel under the hood. `OPENSPINE_OLLAMA_URL`
   names the base URL — `/v1/embeddings` is appended at call time.

3. **Embedding fallback.** On any failure (provider down, model not
   pulled, timeout) `embed_text()` falls back to a deterministic
   SHA-derived pseudo-embedding so CI and the test suite can
   exercise the full pipeline without a model. The pseudo-vector is
   stable for a given text, so search-by-same-text matches.
   Production deployments must pull the real model; the
   deterministic fallback exists for development affordance only.

4. **Default model.** `qwen3-embedding:0.6b` — Alibaba Qwen team's
   June 2025 release. 1024-d native, supports MRL truncation.
   639 MB on disk (Q4_K_M). MTEB-multilingual 64.33, beats
   nomic-embed-text and bge-small at the 0.6B tier. Instruction-
   aware: queries get `Instruct: {task}\\nQuery: {text}` prefix;
   documents are embedded raw.

5. **Qdrant collection lazy-create.** First write to a tenant's
   collection ensures it exists with the correct vector size.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
import uuid
from typing import Any

import httpx
import structlog
from qdrant_client import AsyncQdrantClient, models

from openspine.config import get_settings
from openspine.core.events import Event, get_event_bus
from openspine.core.qdrant import collection_name

logger = structlog.get_logger(__name__)

# Vector size — qwen3-embedding:0.6b emits 1024-d natively. MRL
# truncation to {32..1024} is supported by the model; we use full 1024.
# The deterministic fallback emits the same dimension so we can reuse
# one collection across "real model up" and "model down" runs.
VECTOR_SIZE = 1024
INDEXED_ENTITIES = ("business_partner", "material")
CONSUMER_NAME = "embedding-indexer"


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


async def embed_text(text: str, *, ollama_url: str, model: str) -> list[float]:
    """Embed `text` via the OpenAI-compatible `/v1/embeddings` endpoint.

    Works against Ollama, llama-server, or any other service that
    speaks the OpenAI embeddings shape. On any failure (service down,
    model not pulled, network blip, timeout) falls back to a
    deterministic SHA-derived pseudo-embedding so the search-by-same-
    text path still works for CI and dev. Production must pull the
    real model.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{ollama_url.rstrip('/')}/v1/embeddings",
                json={"model": model, "input": text},
            )
        if response.status_code == 200:
            body = response.json()
            data = body.get("data") or []
            if data and isinstance(data, list):
                vec = data[0].get("embedding")
                if isinstance(vec, list) and len(vec) > 0:
                    return _normalise(vec, VECTOR_SIZE)
    except Exception as exc:
        logger.debug("embedding.provider_unavailable", error=type(exc).__name__)

    return _deterministic_pseudo_embedding(text)


def _normalise(vec: list[float], target_dim: int) -> list[float]:
    if len(vec) == target_dim:
        return vec
    if len(vec) > target_dim:
        return vec[:target_dim]
    return vec + [0.0] * (target_dim - len(vec))


def _deterministic_pseudo_embedding(text: str) -> list[float]:
    """SHA-512 → packed floats, repeated to fill the target dim.

    Stable, fast, no dependencies. NOT a real embedding — it does not
    capture semantic similarity at all. Two semantically related
    inputs ("kg" and "kilogram") produce wildly different vectors.
    Acceptable as a development affordance because the search test
    looks for exact-text round-trips.
    """
    digest = hashlib.sha512(text.encode("utf-8")).digest()  # 64 bytes
    # 64 bytes / 4 bytes per float32 = 16 floats per digest.
    floats: list[float] = []
    while len(floats) < VECTOR_SIZE:
        for i in range(0, 64, 4):
            (val,) = struct.unpack("<i", digest[i : i + 4])
            # Map int32 to roughly [-1, 1].
            floats.append(val / (2**31))
            if len(floats) >= VECTOR_SIZE:
                break
        # Re-hash so successive 16-float blocks differ from each other.
        digest = hashlib.sha512(digest).digest()
    return floats[:VECTOR_SIZE]


# ---------------------------------------------------------------------------
# Qdrant client + collection management
# ---------------------------------------------------------------------------


_client: AsyncQdrantClient | None = None
_ensured_collections: set[str] = set()


def get_qdrant_client(qdrant_url: str | None = None) -> AsyncQdrantClient:
    """Return the process-wide Qdrant client. Lazy because import-time
    failure (Qdrant down in dev) shouldn't crash the FastAPI process."""
    global _client
    if _client is None:
        url = qdrant_url or get_settings().qdrant_url
        _client = AsyncQdrantClient(url=url)
    return _client


def reset_qdrant_state() -> None:
    """Test helper. Clears the cached client + the ensured-collection set
    so the next call rebuilds against a (possibly different) Qdrant."""
    global _client
    _client = None
    _ensured_collections.clear()


async def ensure_collection(client: AsyncQdrantClient, collection: str) -> None:
    """Idempotent: create the collection if missing. Cached per-process."""
    if collection in _ensured_collections:
        return
    try:
        existing = await client.get_collections()
        names = {c.name for c in existing.collections}
        if collection not in names:
            await client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(
                    size=VECTOR_SIZE, distance=models.Distance.COSINE
                ),
            )
        _ensured_collections.add(collection)
    except Exception as exc:
        logger.warning(
            "indexer.collection_ensure_failed",
            collection=collection,
            error=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Upsert + search
# ---------------------------------------------------------------------------


async def upsert_entity(
    *,
    tenant_id: str,
    entity: str,
    entity_id: str,
    text: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Embed `text` and upsert as a Qdrant point for `(tenant, entity, id)`.

    Best-effort: if Qdrant is unreachable, log and return. Reconciliation
    catches up on the next run.
    """
    settings = get_settings()
    client = get_qdrant_client(settings.qdrant_url)
    coll = collection_name(tenant_id)
    await ensure_collection(client, coll)
    vector = await embed_text(text, ollama_url=settings.ollama_url, model=settings.embedding_model)
    point_payload = {
        "entity": entity,
        "entity_id": entity_id,
        "text": text,
        **(payload or {}),
    }
    try:
        await client.upsert(
            collection_name=coll,
            points=[
                models.PointStruct(
                    id=_point_id(entity, entity_id),
                    vector=vector,
                    payload=point_payload,
                )
            ],
        )
    except Exception as exc:
        logger.warning(
            "indexer.upsert_failed",
            collection=coll,
            entity=entity,
            entity_id=entity_id,
            error=type(exc).__name__,
        )


def _point_id(entity: str, entity_id: str) -> str:
    """Deterministic Qdrant point id derived from (entity, uuid).

    Qdrant accepts uuid strings as point ids; we want one point per
    (entity, entity_id) tuple in a tenant's collection so reupserts
    overwrite cleanly. UUID5 over "entity:id" is stable.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{entity}:{entity_id}"))


async def search(
    *,
    tenant_id: str,
    entity: str,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Semantic search: embed the query, find top-K nearest in the
    tenant's collection (filtered by entity). Returns hit dicts:
    `{"entity_id": ..., "text": ..., "score": ..., "payload": {...}}`.
    Returns [] on any failure — caller falls back to ILIKE.
    """
    settings = get_settings()
    client = get_qdrant_client(settings.qdrant_url)
    coll = collection_name(tenant_id)
    try:
        vector = await embed_text(
            query, ollama_url=settings.ollama_url, model=settings.embedding_model
        )
        result = await client.query_points(
            collection_name=coll,
            query=vector,
            limit=limit,
            with_payload=True,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="entity",
                        match=models.MatchValue(value=entity),
                    )
                ]
            ),
        )
    except Exception as exc:
        logger.debug(
            "indexer.search_failed",
            collection=coll,
            error=type(exc).__name__,
        )
        return []

    hits: list[dict[str, Any]] = []
    for point in result.points:
        payload = dict(point.payload or {})
        hits.append(
            {
                "entity_id": payload.get("entity_id"),
                "text": payload.get("text"),
                "score": point.score,
                "payload": payload,
            }
        )
    return hits


# ---------------------------------------------------------------------------
# Event handler — registered against the bus at app startup
# ---------------------------------------------------------------------------


async def handle_event(event: Event) -> None:
    """Bus subscriber. Dispatches to upsert_entity for the entities we index."""
    parts = event.stream.split(".")
    # Expected shape: master_data.<entity>.<verb>
    if len(parts) < 3 or parts[0] != "master_data":
        return
    entity = parts[1]
    if entity not in INDEXED_ENTITIES:
        return
    payload = event.payload or {}
    text = payload.get("indexable_text") or payload.get("name") or ""
    entity_id = payload.get("id")
    if not entity_id or not text:
        return
    await upsert_entity(
        tenant_id=event.tenant_id,
        entity=entity,
        entity_id=str(entity_id),
        text=str(text),
        payload={k: v for k, v in payload.items() if k != "indexable_text"},
    )


_registered = False


async def register_indexer() -> None:
    """Subscribe `handle_event` to `master_data.**` on the process bus.

    Idempotent — re-registration is a noop. Called from FastAPI
    lifespan startup; tests that use a fresh InMemoryEventBus call
    this themselves to wire the indexer up.
    """
    global _registered
    if _registered:
        return
    bus = get_event_bus()
    await bus.subscribe("master_data.**", handle_event, consumer=CONSUMER_NAME)
    _registered = True
    logger.info("indexer.registered", consumer=CONSUMER_NAME)


def reset_registration() -> None:
    """Test helper. Clears the registered flag so a fresh bus can rewire."""
    global _registered
    _registered = False


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def bp_indexable_text(name: str, number: str, country_code: str | None) -> str:
    """One canonical formula shared by the publisher and the reconciler.

    Embedding text MUST match between create-time and reconcile-time —
    a deterministic-fallback embed for "name + number" can't be
    retrieved by a "name + number" query if the reconcile only embedded
    "name". With Ollama in production, similar texts produce similar
    vectors; with the dev fallback we need exact-text equality.
    """
    return f"{name} {number} {country_code or ''}".strip()


def material_indexable_text(description: str, number: str) -> str:
    return f"{description} {number}".strip()


async def reconcile_tenant(*, tenant_id: str, session: Any) -> dict[str, int]:
    """Walk MD tables and re-index every row.

    Used to recover from "we lost Qdrant" — the v0.1 §3 #4
    acceptance criterion. Returns counts per entity.

    Clears the in-process collection-existence cache so that a
    collection dropped out-of-band gets recreated by the first
    upsert.
    """
    from sqlalchemy import select

    from openspine.md.models import MdBusinessPartner, MdMaterial

    _ensured_collections.discard(collection_name(tenant_id))
    counts = {"business_partner": 0, "material": 0}

    bp_rows = (
        (
            await session.execute(
                select(MdBusinessPartner).where(MdBusinessPartner.tenant_id == uuid.UUID(tenant_id))
            )
        )
        .scalars()
        .all()
    )
    for bp in bp_rows:
        await upsert_entity(
            tenant_id=tenant_id,
            entity="business_partner",
            entity_id=str(bp.id),
            text=bp_indexable_text(bp.name, bp.number, bp.country_code),
            payload={
                "id": str(bp.id),
                "number": bp.number,
                "name": bp.name,
                "country_code": bp.country_code,
            },
        )
        counts["business_partner"] += 1

    mat_rows = (
        (
            await session.execute(
                select(MdMaterial).where(MdMaterial.tenant_id == uuid.UUID(tenant_id))
            )
        )
        .scalars()
        .all()
    )
    for m in mat_rows:
        await upsert_entity(
            tenant_id=tenant_id,
            entity="material",
            entity_id=str(m.id),
            text=material_indexable_text(m.description, m.number),
            payload={
                "id": str(m.id),
                "number": m.number,
                "description": m.description,
                "material_type": m.material_type,
            },
        )
        counts["material"] += 1

    return counts


# Ensure the asyncio module is referenced so type-checkers don't prune it.
_ = asyncio


__all__ = [
    "CONSUMER_NAME",
    "INDEXED_ENTITIES",
    "VECTOR_SIZE",
    "embed_text",
    "ensure_collection",
    "get_qdrant_client",
    "handle_event",
    "reconcile_tenant",
    "register_indexer",
    "reset_qdrant_state",
    "reset_registration",
    "search",
    "upsert_entity",
]
