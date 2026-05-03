"""Identity, authentication, RBAC, and audit.

Tables prefixed `id_*`. See `docs/identity/` for the specification.

The §4.2 cut covers tenant + principal (human/agent/technical) +
credential + session + token + federated-identity stub + audit-event.
The §4.3 cut adds the auth-object catalogue, two-tier role model,
permissions, principal-role assignments, SoD rules + overrides, and
the append-only auth decision log.

Importing this package registers all identity ORM models on the shared
`openspine.core.database.metadata`, which is what Alembic and the
schema-invariants test introspect.
"""

from openspine.identity import models as models
from openspine.identity import rbac_models as rbac_models
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
from openspine.identity.rbac_models import (
    IdAgentDecisionTrace,
    IdAuthDecisionLog,
    IdAuthObject,
    IdAuthObjectAction,
    IdAuthObjectQualifier,
    IdPermission,
    IdPrincipalRole,
    IdRoleComposite,
    IdRoleCompositeMember,
    IdRoleSingle,
    IdSodOverride,
    IdSodRule,
    IdSodRuleClause,
)

__all__ = [
    "IdAgentDecisionTrace",
    "IdAgentProfile",
    "IdAuditEvent",
    "IdAuthDecisionLog",
    "IdAuthObject",
    "IdAuthObjectAction",
    "IdAuthObjectQualifier",
    "IdCredential",
    "IdFederatedIdentity",
    "IdHumanProfile",
    "IdPermission",
    "IdPrincipal",
    "IdPrincipalRole",
    "IdRoleComposite",
    "IdRoleCompositeMember",
    "IdRoleSingle",
    "IdSession",
    "IdSodOverride",
    "IdSodRule",
    "IdSodRuleClause",
    "IdTenant",
    "IdTenantSetting",
    "IdToken",
]
