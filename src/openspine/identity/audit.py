"""Append-only audit-event writer (`id_audit_event`).

Per `docs/identity/README.md` §"Audit topology", this is the
"what happened" stream. Authorisation decisions go to
`id_auth_decision_log` (lands §4.3); agent reasoning goes to
`id_agent_decision_trace` (lands §4.7). The three are joined by
`trace_id` for cross-stream investigation.

The writer is intentionally simple: take a session, take the event
shape, INSERT. The auth flow is the only caller in §4.2; later modules
will call this from their service layer for `business_data.*` events.

It does NOT batch. Per `docs/identity/permissions.md` §"Performance
notes", the *decision-log* writes (high volume, every authorisation
check) are async-batched. `id_audit_event` writes are one-per-business-
action (low volume) and fire synchronously so the audit row commits
in the same transaction as the action it records. If the action rolls
back, the audit rolls back with it — this is intentional.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from openspine.identity.models import AUDIT_OUTCOMES, IdAuditEvent


async def write_audit_event(
    session: AsyncSession,
    *,
    action: str,
    outcome: str,
    trace_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    principal_id: uuid.UUID | None = None,
    target_kind: str | None = None,
    target_id: uuid.UUID | None = None,
    reason: str | None = None,
    event_metadata: dict[str, Any] | None = None,
    created_by: uuid.UUID | None = None,
) -> IdAuditEvent:
    """Insert an `id_audit_event` row.

    `outcome` must be one of `AUDIT_OUTCOMES` ('success' or 'failure').
    `trace_id` is required — an event without a trace_id cannot be
    correlated to its parent operation, which defeats the audit.
    `tenant_id`/`principal_id` are optional: failed login attempts
    against unknown tenants and unauthenticated requests still write
    audit rows (with the relevant fields NULL).

    The caller is responsible for committing the surrounding
    transaction. The new row is returned (with its assigned `id`) so
    callers can chain reference rows (e.g., agent decision traces).
    """
    if outcome not in AUDIT_OUTCOMES:
        raise ValueError(f"outcome must be one of {AUDIT_OUTCOMES}, got {outcome!r}")
    row = IdAuditEvent(
        tenant_id=tenant_id,
        trace_id=trace_id,
        principal_id=principal_id,
        action=action,
        target_kind=target_kind,
        target_id=target_id,
        outcome=outcome,
        reason=reason,
        event_metadata=event_metadata or {},
        created_by=created_by if created_by is not None else principal_id,
    )
    session.add(row)
    await session.flush()
    return row


__all__ = ["write_audit_event"]
