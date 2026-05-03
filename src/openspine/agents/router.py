"""Agent surface HTTP endpoints — `/agents/*`.

POST /agents/traces — an agent posts the reasoning behind an action.
The caller must BE an agent principal (kind='agent'); humans cannot
write to this stream (humans use `id_audit_event` instead, which the
service layer writes automatically).

The endpoint deliberately does NOT gate via `@requires_auth` — the
agent-only check below is the gate. Agents always get to write
their own decision traces; that's an inherent affordance of being
an agent in OpenSpine.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from openspine.agents.service import write_agent_decision_trace
from openspine.core.errors import AuthenticationError, AuthorisationError
from openspine.identity.context import PrincipalContext
from openspine.identity.middleware import get_request_session

router = APIRouter(prefix="/agents", tags=["identity"])


class AgentTraceIn(BaseModel):
    action_summary: str = Field(min_length=1, max_length=500)
    reasoning: str = Field(min_length=1)
    candidates_considered: list[Any] = Field(default_factory=list)
    chosen_path: dict[str, Any] = Field(default_factory=dict)
    related_audit_event_id: uuid.UUID | None = None
    model: str | None = None
    model_version: str | None = None


class AgentTraceOut(BaseModel):
    id: uuid.UUID
    trace_id: uuid.UUID


@router.post(
    "/traces",
    response_model=AgentTraceOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_agent_trace(payload: AgentTraceIn, request: Request) -> AgentTraceOut:
    """Write an agent decision trace.

    Caller must be an agent principal. Cross-principal writes are
    blocked: a trace is always *about* the calling agent.
    """
    ctx: PrincipalContext = getattr(request.state, "principal_context", None) or (
        PrincipalContext.anonymous(trace_id=uuid.uuid4())
    )
    if ctx.is_anonymous:
        raise AuthenticationError(
            "authentication required",
            domain="auth",
            action="access",
            reason="not_authenticated",
        )
    if ctx.principal_kind != "agent":
        raise AuthorisationError(
            "only agent principals can write decision traces",
            domain="agents.trace",
            action="write",
            reason="not_an_agent",
        )

    session = get_request_session()
    assert ctx.tenant_id is not None
    assert ctx.principal_id is not None
    row = await write_agent_decision_trace(
        session,
        tenant_id=ctx.tenant_id,
        principal_id=ctx.principal_id,
        trace_id=ctx.trace_id,
        action_summary=payload.action_summary,
        reasoning=payload.reasoning,
        candidates_considered=payload.candidates_considered,
        chosen_path=payload.chosen_path,
        related_audit_event_id=payload.related_audit_event_id,
        model=payload.model,
        model_version=payload.model_version,
    )
    return AgentTraceOut(id=row.id, trace_id=row.trace_id)


__all__ = ["router"]
