"""Tests for the hook registry and dispatcher."""

from __future__ import annotations

import pytest

from openspine.core import hooks


@pytest.fixture(autouse=True)
def _reset_hooks() -> None:
    hooks.reset()


def test_register_and_dispatch_pre_sync() -> None:
    calls: list[str] = []

    @hooks.hook("widget.pre_save")
    def handler(payload: dict[str, str]) -> None:
        calls.append(payload["name"])

    import asyncio

    asyncio.run(hooks.dispatch_pre("widget.pre_save", {"name": "alpha"}))
    assert calls == ["alpha"]


def test_pre_hook_aborts_when_handler_raises() -> None:
    @hooks.hook("widget.pre_save")
    def handler(payload: dict[str, str]) -> None:
        raise ValueError("nope")

    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(hooks.dispatch_pre("widget.pre_save", {}))


def test_post_hook_swallows_exceptions() -> None:
    @hooks.hook("widget.post_save", async_=True)
    def handler(payload: dict[str, str]) -> None:
        raise RuntimeError("expected — should be swallowed")

    import asyncio

    # Should not raise.
    asyncio.run(hooks.dispatch_post("widget.post_save", {}))


def test_registered_hooks_introspection() -> None:
    @hooks.hook("a.pre_post")
    def _a() -> None:
        pass

    @hooks.hook("b.post_post", async_=True)
    def _b() -> None:
        pass

    snapshot = hooks.registered_hooks()
    assert snapshot["pre"]["a.pre_post"] == 1
    assert snapshot["post"]["b.post_post"] == 1


def test_post_prefix_inferred_when_async_flag_omitted() -> None:
    @hooks.hook("widget.post_save")
    async def handler() -> None:
        pass

    snapshot = hooks.registered_hooks()
    assert "widget.post_save" in snapshot["post"]
    assert "widget.post_save" not in snapshot["pre"]
