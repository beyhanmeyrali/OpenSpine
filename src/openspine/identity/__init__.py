"""Identity, authentication, RBAC, and audit.

Tables prefixed `id_*`. See `docs/identity/` for the specification.

The §4.2 cut lands here: tenant + principal (human/agent/technical) +
credential + session + token + federated-identity stub + audit-event.
RBAC and the auth-object engine land in §4.3 (`docs/identity/permissions.md`).

Importing this package registers the identity ORM models on the shared
`openspine.core.database.metadata`, which is what Alembic and the
schema-invariants test introspect.
"""

from openspine.identity import models as models
from openspine.identity.models import (
    IdAgentProfile,
    IdAuditEvent,
    IdCredential,
    IdFederatedIdentity,
    IdHumanProfile,
    IdPrincipal,
    IdSession,
    IdTenant,
    IdTenantSetting,
    IdToken,
)

__all__ = [
    "IdAgentProfile",
    "IdAuditEvent",
    "IdCredential",
    "IdFederatedIdentity",
    "IdHumanProfile",
    "IdPrincipal",
    "IdSession",
    "IdTenant",
    "IdTenantSetting",
    "IdToken",
]
