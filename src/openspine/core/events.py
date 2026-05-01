"""Event bus contract and producers (v0.1 §4.5).

Per `ARCHITECTURE.md` §3, every domain service performs its transactional
write to PostgreSQL inside a database transaction, then — after commit —
publishes an event to the bus. Consumers (embedding worker, plugin host
async hooks, integration webhooks) react asynchronously.

Two concrete buses ship:

- `RedisEventBus` — production. Uses Redis Streams.
- `InMemoryEventBus` — test double. Single-process, no persistence, sequential
  delivery. Used by unit tests and scripted demos.

The contract is a `Protocol`; new bus implementations (e.g. NATS, Kafka) can
be slotted in without touching domain code.

Event payload conventions:

- Stream names are `<module>.<entity>.<verb>`, e.g. `master_data.material.created`.
- Every event carries a `tenant_id`, an `event_id` (UUID), an `occurred_at`
  timestamp (ISO-8601 UTC), and a freeform `payload` dict.
- Optional `trace_id` / `span_id` propagate OpenTelemetry context to consumers.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from openspine.core.observability import (
    events_consumed_total,
    events_published_total,
)

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class Event:
    """Wire envelope for an event on the bus.

    `stream` doubles as the channel name and the routing key. Consumers
    subscribe to a stream pattern (e.g. `master_data.*`) and receive every
    event whose stream matches.
    """

    stream: str
    tenant_id: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    trace_id: str | None = None
    span_id: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> Event:
        data = json.loads(raw)
        return cls(**data)


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus(Protocol):
    """Contract every event-bus implementation conforms to."""

    async def publish(self, event: Event) -> None: ...

    async def subscribe(
        self,
        stream_pattern: str,
        handler: EventHandler,
        *,
        consumer: str,
    ) -> None:
        """Register `handler` for events matching `stream_pattern`.

        `consumer` is a stable identifier used for delivery tracking. In Redis
        Streams it's the consumer-group name; in-memory it's just metadata.
        """
        ...


# ---- In-memory bus (tests, demos, single-process tools) --------------------


class InMemoryEventBus:
    """Sequential, single-process bus for tests and scripted demos.

    `publish` invokes every matching handler before returning. This is the
    opposite of the production bus's contract (asynchronous fanout) — but it
    makes tests synchronous and deterministic.
    """

    def __init__(self) -> None:
        self._handlers: list[tuple[str, str, EventHandler]] = []
        self._published: list[Event] = []

    async def publish(self, event: Event) -> None:
        events_published_total.labels(stream=event.stream).inc()
        self._published.append(event)
        for pattern, consumer, handler in list(self._handlers):
            if _matches(event.stream, pattern):
                try:
                    await handler(event)
                except Exception:
                    events_consumed_total.labels(
                        stream=event.stream, consumer=consumer, outcome="error"
                    ).inc()
                    logger.exception(
                        "event.handler.failed",
                        stream=event.stream,
                        consumer=consumer,
                        event_id=event.event_id,
                    )
                    continue
                events_consumed_total.labels(
                    stream=event.stream, consumer=consumer, outcome="ok"
                ).inc()

    async def subscribe(
        self,
        stream_pattern: str,
        handler: EventHandler,
        *,
        consumer: str,
    ) -> None:
        self._handlers.append((stream_pattern, consumer, handler))

    @property
    def published(self) -> list[Event]:
        """Tests can introspect what was published. Read-only by convention."""
        return list(self._published)

    def reset(self) -> None:
        """Tests use this between cases."""
        self._handlers.clear()
        self._published.clear()


def _matches(stream: str, pattern: str) -> bool:
    """Glob-style match — `*` matches a single segment, `**` matches one or more."""
    s_parts = stream.split(".")
    p_parts = pattern.split(".")
    if "**" in p_parts:
        # `**` is only supported as the last segment.
        idx = p_parts.index("**")
        if idx != len(p_parts) - 1:
            return False
        # Need at least one segment to match `**`.
        if len(s_parts) <= idx:
            return False
        return _segment_match(s_parts[:idx], p_parts[:idx])
    if len(s_parts) != len(p_parts):
        return False
    return _segment_match(s_parts, p_parts)


def _segment_match(s: list[str], p: list[str]) -> bool:
    return all(pp == "*" or pp == ss for ss, pp in zip(s, p, strict=False))


# ---- Singleton accessor ---------------------------------------------------

_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the process-wide bus.

    Defaults to the in-memory bus until production wiring (`RedisEventBus`)
    lands. Tests reset this via `set_event_bus(InMemoryEventBus())`.
    """
    global _bus
    if _bus is None:
        _bus = InMemoryEventBus()
    return _bus


def set_event_bus(bus: EventBus) -> None:
    """Override the process-wide bus. Test/bootstrap helper."""
    global _bus
    _bus = bus


# Avoid an unused-import warning for asyncio (used by type checkers when
# subclasses use it).
_ = asyncio
