"""Agent decision-trace service.

Inserts `id_agent_decision_trace` rows. Only agent principals can
write traces about themselves; the route layer enforces the
`kind == 'agent'` check before calling here.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from openspine.identity.rbac_models import IdAgentDecisionTrace


async def write_agent_decision_trace(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    trace_id: uuid.UUID,
    action_summary: str,
    reasoning: str,
    candidates_considered: list[Any] | None = None,
    chosen_path: dict[str, Any] | None = None,
    related_audit_event_id: uuid.UUID | None = None,
    model: str | None = None,
    model_version: str | None = None,
) -> IdAgentDecisionTrace:
    row = IdAgentDecisionTrace(
        tenant_id=tenant_id,
        principal_id=principal_id,
        trace_id=trace_id,
        action_summary=action_summary,
        reasoning=reasoning,
        candidates_considered=candidates_considered or [],
        chosen_path=chosen_path or {},
        related_audit_event_id=related_audit_event_id,
        model=model,
        model_version=model_version,
    )
    session.add(row)
    await session.flush()
    return row


__all__ = ["write_agent_decision_trace"]
