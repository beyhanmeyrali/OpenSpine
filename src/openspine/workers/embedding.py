"""Embedding worker (v0.1 §4.5).

Run with `python -m openspine.workers.embedding` (or `make worker`).

Subscribes to every `master_data.*.created|updated` stream, generates a vector
embedding via Ollama (`OPENSPINE_EMBEDDING_MODEL`, default `qwen2.5:1.5b`),
and upserts into the per-tenant Qdrant collection.

The worker is intentionally simple in v0.1 — no batching, no retries beyond
the bus's at-least-once delivery, no smart back-off. Reconciliation handles
gaps. Performance tuning lands when we have data on real consumer lag.

This file currently ships the **structural skeleton**: subscription, logging,
metrics. The Ollama and Qdrant clients are wired in once the rest of v0.1
lands and there are real entities to index.
"""

from __future__ import annotations

import asyncio

import structlog

from openspine.config import get_settings
from openspine.core.events import Event, get_event_bus
from openspine.core.logging import configure_logging
from openspine.core.observability import configure_tracing

logger = structlog.get_logger(__name__)

CONSUMER_NAME = "embedding-worker"


async def handle_event(event: Event) -> None:
    """Process a single event: embed payload, upsert into Qdrant.

    v0.1 skeleton — logs the event and emits the consumed metric. Embedding +
    Qdrant upsert wire in alongside the first MD entity in §4.4.
    """
    logger.info(
        "embedding.event.received",
        stream=event.stream,
        tenant_id=event.tenant_id,
        event_id=event.event_id,
    )
    # TODO(v0.1 §4.5): generate embedding via Ollama and upsert into the
    # per-tenant Qdrant collection. Until then this is a no-op so the bus
    # contract and metrics surface are testable independently.


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings)
    bus = get_event_bus()

    logger.info("embedding.worker.starting", consumer=CONSUMER_NAME)
    await bus.subscribe("master_data.**", handle_event, consumer=CONSUMER_NAME)
    logger.info("embedding.worker.subscribed", pattern="master_data.**")

    # Keep the process alive. Real worker will use the bus's blocking consume
    # loop; the in-memory bus delivers synchronously on publish.
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logger.info("embedding.worker.shutdown")
        raise


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
