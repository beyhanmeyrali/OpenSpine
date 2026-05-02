"""Tests for the event bus contract and the in-memory implementation."""

from __future__ import annotations

import asyncio
import json

import pytest

from openspine.core.events import Event, InMemoryEventBus, _matches


def test_event_serialises_round_trip() -> None:
    e = Event(
        stream="master_data.material.created",
        tenant_id="t-1",
        payload={"id": "m-1", "name": "Steel rod"},
    )
    raw = e.to_json()
    parsed = json.loads(raw)
    assert parsed["stream"] == "master_data.material.created"
    assert parsed["tenant_id"] == "t-1"
    assert parsed["payload"]["name"] == "Steel rod"
    assert parsed["event_id"] == e.event_id

    e2 = Event.from_json(raw)
    assert e2 == e


def test_publish_invokes_matching_subscriber() -> None:
    bus = InMemoryEventBus()
    seen: list[Event] = []

    async def handler(event: Event) -> None:
        seen.append(event)

    asyncio.run(bus.subscribe("master_data.material.*", handler, consumer="t"))
    asyncio.run(
        bus.publish(
            Event(
                stream="master_data.material.created",
                tenant_id="t-1",
                payload={"id": "m-1"},
            )
        )
    )
    assert len(seen) == 1
    assert seen[0].stream == "master_data.material.created"


def test_publish_skips_non_matching_subscriber() -> None:
    bus = InMemoryEventBus()
    seen: list[Event] = []

    async def handler(event: Event) -> None:
        seen.append(event)

    asyncio.run(bus.subscribe("master_data.bp.*", handler, consumer="t"))
    asyncio.run(
        bus.publish(Event(stream="master_data.material.created", tenant_id="t-1", payload={}))
    )
    assert seen == []


def test_double_star_pattern_matches_any_depth() -> None:
    bus = InMemoryEventBus()
    seen: list[str] = []

    async def handler(event: Event) -> None:
        seen.append(event.stream)

    asyncio.run(bus.subscribe("master_data.**", handler, consumer="t"))
    for stream in (
        "master_data.material.created",
        "master_data.bp.updated",
        "master_data.gl_account.created",
    ):
        asyncio.run(bus.publish(Event(stream=stream, tenant_id="t-1", payload={})))
    assert sorted(seen) == sorted(
        [
            "master_data.material.created",
            "master_data.bp.updated",
            "master_data.gl_account.created",
        ]
    )


def test_handler_exception_does_not_break_other_handlers() -> None:
    bus = InMemoryEventBus()
    flaky_calls = 0
    healthy_calls = 0

    async def flaky(event: Event) -> None:
        nonlocal flaky_calls
        flaky_calls += 1
        raise RuntimeError("boom")

    async def healthy(event: Event) -> None:
        nonlocal healthy_calls
        healthy_calls += 1

    asyncio.run(bus.subscribe("a.*", flaky, consumer="flaky"))
    asyncio.run(bus.subscribe("a.*", healthy, consumer="healthy"))
    asyncio.run(bus.publish(Event(stream="a.created", tenant_id="t-1", payload={})))
    assert flaky_calls == 1
    assert healthy_calls == 1


def test_published_history_visible_to_tests() -> None:
    bus = InMemoryEventBus()
    asyncio.run(bus.publish(Event(stream="x.created", tenant_id="t-1", payload={"k": "v"})))
    assert len(bus.published) == 1
    assert bus.published[0].stream == "x.created"


def test_reset_clears_subscribers_and_history() -> None:
    bus = InMemoryEventBus()

    async def handler(event: Event) -> None:
        pass

    asyncio.run(bus.subscribe("x.*", handler, consumer="t"))
    asyncio.run(bus.publish(Event(stream="x.created", tenant_id="t-1", payload={})))
    bus.reset()
    assert bus.published == []


@pytest.mark.parametrize(
    ("stream", "pattern", "expected"),
    [
        ("a.b.c", "a.b.c", True),
        ("a.b.c", "a.*.c", True),
        ("a.b.c", "a.b.*", True),
        ("a.b.c", "*.b.c", True),
        ("a.b.c", "a.b.d", False),
        ("a.b.c", "a.b", False),
        ("a.b", "a.b.c", False),
        ("a.b.c", "a.**", True),
        ("a.b.c.d", "a.**", True),
        ("a", "**", True),
        ("a.b.c", "*", False),  # `*` is a single segment, doesn't match deeper paths
    ],
)
def test_pattern_matching_table(stream: str, pattern: str, expected: bool) -> None:
    assert _matches(stream, pattern) is expected
