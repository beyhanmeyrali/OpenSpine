"""Hook registry — the plugin extension surface.

Per `ARCHITECTURE.md` §6.3 every business transaction exposes a deliberate,
documented, versioned set of hook points. Plugins register handlers via
`@hook(name)` and the domain services dispatch through `dispatch_pre` /
`dispatch_post`.

`pre_*` hooks run synchronously inside the service transaction and may raise
`ValidationError` / `AuthorisationError` to abort. `post_*` hooks run
asynchronously after commit, fanned out via the event bus.

Naming convention is `entity.{pre,post}_{verb}` per `docs/README.md:31`. The
registry does not enforce the convention yet — hook-name reconciliation is a
v0.1 blocker tracked in the v0.1-foundation plan §6.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

import structlog

logger = structlog.get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")
HookHandler = Callable[..., Any]

_pre_handlers: dict[str, list[HookHandler]] = defaultdict(list)
_post_handlers: dict[str, list[HookHandler]] = defaultdict(list)


def hook(name: str, *, async_: bool = False) -> Callable[[HookHandler], HookHandler]:
    """Register a handler for a hook name.

    By convention `pre_*` hooks register as sync handlers (they run inside the
    service transaction); `post_*` hooks register with `async_=True` and run
    via the event bus after commit.
    """

    def decorator(fn: HookHandler) -> HookHandler:
        target = _post_handlers if async_ or name.split(".", 1)[-1].startswith("post_") else _pre_handlers
        target[name].append(fn)
        logger.info("hook.registered", hook=name, handler=fn.__qualname__)
        return fn

    return decorator


async def dispatch_pre(name: str, /, *args: Any, **kwargs: Any) -> None:
    """Run every pre-hook for `name` in registration order.

    Handlers may be sync or async. If a handler raises, the dispatch aborts
    and the exception propagates so the calling service can roll back.
    """

    handlers = _pre_handlers.get(name, [])
    for handler in handlers:
        result = handler(*args, **kwargs)
        if inspect.isawaitable(result):
            await result


async def dispatch_post(name: str, /, *args: Any, **kwargs: Any) -> None:
    """Run every post-hook for `name`.

    Handlers run in registration order. Failures are logged but do not
    propagate — post-hooks are advisory and async by contract; durable
    side-effects belong on the event bus instead.
    """

    handlers = _post_handlers.get(name, [])
    for handler in handlers:
        try:
            result = handler(*args, **kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("hook.post.failed", hook=name, handler=handler.__qualname__)


def registered_hooks() -> dict[str, dict[str, int]]:
    """Introspection helper — used by the `/system/hooks` endpoint."""
    return {
        "pre": {name: len(hs) for name, hs in _pre_handlers.items()},
        "post": {name: len(hs) for name, hs in _post_handlers.items()},
    }


def reset() -> None:
    """Clear all registrations. Test-only."""
    _pre_handlers.clear()
    _post_handlers.clear()


__all__ = [
    "dispatch_post",
    "dispatch_pre",
    "hook",
    "registered_hooks",
    "reset",
]


# Silence the unused-import warning for asyncio (used by type checkers when
# handlers return coroutines).
_ = asyncio
