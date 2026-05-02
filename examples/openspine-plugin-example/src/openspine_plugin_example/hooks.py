"""Hook handlers for the example plugin.

Each function is registered against a named hook via the @hook decorator.
The plugin host imports this module at load time so registrations take
effect.
"""

from __future__ import annotations

from typing import Any

import structlog

from openspine.core.hooks import hook

logger = structlog.get_logger(__name__)


@hook("business_partner.pre_save")
def validate_business_partner(ctx: Any, bp: Any) -> None:
    """Demonstrates a pre-save validation hook.

    A real plugin would inspect `bp` (the business partner being saved)
    and raise `openspine.core.errors.ValidationError` to abort the save
    if the business rule is violated. This example just logs.
    """
    logger.info("example.bp.pre_save", bp=getattr(bp, "id", None))


@hook("material.post_save", async_=True)
async def audit_material_save(ctx: Any, material: Any) -> None:
    """Demonstrates a post-save async side-effect hook.

    Post-hooks fire after the service transaction has committed and run
    out-of-band (no abort possible). Real plugins use this for emails,
    integrations, cache warming, etc.
    """
    logger.info("example.material.post_save", material=getattr(material, "id", None))
