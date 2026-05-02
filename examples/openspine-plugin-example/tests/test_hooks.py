"""Tests for the example plugin's hook handlers.

These tests double as the canonical pattern for plugin authors. They:

- Exercise hook handlers as ordinary Python callables (no plugin host
  required for unit tests).
- Use `openspine.core.hooks.dispatch_pre` / `dispatch_post` when an
  integration test wants to verify the full registration → dispatch
  path.

Run from the example plugin directory:

    pip install -e ../..        # core (editable)
    pip install -e .            # the example itself (editable)
    pytest

In the OpenSpine monorepo, these tests are not collected by the core
pytest run (the rootdir is the repo root and `tests/` there doesn't
include this directory). They are collected when you run pytest from
inside `examples/openspine-plugin-example/`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openspine_plugin_example.hooks import (
    audit_material_save,
    validate_business_partner,
)


def test_validate_business_partner_runs_without_error() -> None:
    """The example pre-save hook is illustrative — it logs but does not abort.

    A real plugin's pre-save hook would raise
    `openspine.core.errors.ValidationError` when its business rule is
    violated. Test that path by passing a payload that should fail and
    asserting the right exception type.
    """
    ctx = SimpleNamespace()
    bp = SimpleNamespace(id="bp-1", country="DE", custom_fields={})
    # Should not raise.
    validate_business_partner(ctx, bp)


def test_audit_material_save_is_async() -> None:
    """The example post-save hook is async — verify it awaits cleanly."""
    ctx = SimpleNamespace()
    material = SimpleNamespace(id="m-1")
    asyncio.run(audit_material_save(ctx, material))


def test_hook_modules_register_handlers_on_import() -> None:
    """Importing the hooks module is what wires the @hook decorators.

    Plugin authors should test that their handlers actually register —
    a typo in the hook name or a missing import would silently leave
    handlers dangling.
    """
    import openspine_plugin_example.hooks  # noqa: F401  (import is the assertion)
    from openspine.core.hooks import registered_hooks

    snapshot = registered_hooks()
    assert "business_partner.pre_save" in snapshot["pre"]
    assert "material.post_save" in snapshot["post"]
