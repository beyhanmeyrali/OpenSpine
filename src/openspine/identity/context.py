"""Principal context — the per-request identity envelope.

Set by the principal-context middleware. Read by service-layer code
(via `request.state` or, in §4.3+, via a FastAPI dependency).

The context is **always** present on `request.state` — anonymous
requests get an `anonymous` context with `tenant_id is None` and
`principal_id is None`. This keeps service code from special-casing
"middleware ran vs didn't run".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PrincipalContext:
    """The authenticated identity context for a single request.

    `effective_roles` is set to `[]` in v0.1; the RBAC engine in §4.3
    populates it from `id_principal_role` joins.

    `auth_method` records how the principal proved themselves —
    `'session'`, `'token'`, or `'anonymous'`. The auth router writes
    this for downstream audit; nothing in v0.1 acts on it directly.
    """

    tenant_id: uuid.UUID | None
    principal_id: uuid.UUID | None
    principal_kind: str | None  # 'human', 'agent', 'technical', or None
    auth_method: str  # 'session', 'token', 'anonymous'
    trace_id: uuid.UUID
    effective_roles: list[uuid.UUID] = field(default_factory=list)

    @property
    def is_anonymous(self) -> bool:
        return self.principal_id is None

    @classmethod
    def anonymous(cls, *, trace_id: uuid.UUID) -> PrincipalContext:
        return cls(
            tenant_id=None,
            principal_id=None,
            principal_kind=None,
            auth_method="anonymous",
            trace_id=trace_id,
        )


__all__ = ["PrincipalContext"]
