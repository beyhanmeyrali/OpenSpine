"""Idempotent seeding of the system catalogue per tenant.

`seed_system_catalogue(session, tenant_id, *, actor_principal_id)` upserts
every auth-object, single role, composite role, and SoD rule listed in
`openspine.identity.system_catalogue` into the given tenant. Running it
again produces no changes (everything keyed on `system_key`).

Call from:
- the bootstrap CLI right after creating a fresh tenant
- the `openspine seed-system-catalogue` admin subcommand to reapply
  (e.g., after a system pack update)

Tenant-owned (`is_system = FALSE`) rows are never touched.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openspine.identity.rbac_models import (
    IdAuthObject,
    IdAuthObjectAction,
    IdAuthObjectQualifier,
    IdPermission,
    IdRoleComposite,
    IdRoleCompositeMember,
    IdRoleSingle,
    IdSodRule,
    IdSodRuleClause,
)
from openspine.identity.system_catalogue import (
    AUTH_OBJECTS,
    COMPOSITE_ROLES,
    SINGLE_ROLES,
    SOD_RULES,
    AuthObjectSeed,
    CompositeRoleSeed,
    SingleRoleSeed,
    SodRuleSeed,
)


async def seed_system_catalogue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
) -> dict[str, int]:
    """Upsert the system catalogue into a tenant. Returns counts created.

    `actor_principal_id` is recorded on every `created_by` / `updated_by`
    column. Typical caller is the bootstrap admin during tenant creation.
    """
    counts = {
        "auth_objects": 0,
        "single_roles": 0,
        "composite_roles": 0,
        "sod_rules": 0,
    }

    # Set the tenant GUC for the session so RLS allows the inserts /
    # selects on tenant-scoped tables.
    from sqlalchemy import text

    await session.execute(
        text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
    )

    counts["auth_objects"] = await _seed_auth_objects(
        session,
        AUTH_OBJECTS,
        tenant_id=tenant_id,
        actor_principal_id=actor_principal_id,
    )
    counts["single_roles"] = await _seed_single_roles(
        session,
        SINGLE_ROLES,
        tenant_id=tenant_id,
        actor_principal_id=actor_principal_id,
    )
    counts["composite_roles"] = await _seed_composite_roles(
        session,
        COMPOSITE_ROLES,
        tenant_id=tenant_id,
        actor_principal_id=actor_principal_id,
    )
    counts["sod_rules"] = await _seed_sod_rules(
        session,
        SOD_RULES,
        tenant_id=tenant_id,
        actor_principal_id=actor_principal_id,
    )

    return counts


# ---------------------------------------------------------------------------
# Per-section seeders
# ---------------------------------------------------------------------------


async def _seed_auth_objects(
    session: AsyncSession,
    seeds: Iterable[AuthObjectSeed],
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
) -> int:
    created = 0
    for seed in seeds:
        existing = await _find_by_system_key(
            session, IdAuthObject, tenant_id=tenant_id, system_key=seed.system_key
        )
        if existing is None:
            obj = IdAuthObject(
                tenant_id=tenant_id,
                domain=seed.domain,
                description=seed.description,
                is_system=True,
                system_key=seed.system_key,
                created_by=actor_principal_id,
                updated_by=actor_principal_id,
            )
            session.add(obj)
            await session.flush()
            existing = obj
            created += 1

        # Upsert actions and qualifiers (idempotent against the unique
        # (auth_object_id, code) constraints).
        for action_code in seed.actions:
            await _ensure_action(
                session,
                auth_object_id=existing.id,
                tenant_id=tenant_id,
                action_code=action_code,
                actor_principal_id=actor_principal_id,
            )
        for qualifier_code, data_type in seed.qualifiers:
            await _ensure_qualifier(
                session,
                auth_object_id=existing.id,
                tenant_id=tenant_id,
                qualifier_code=qualifier_code,
                data_type=data_type,
                actor_principal_id=actor_principal_id,
            )
    return created


async def _seed_single_roles(
    session: AsyncSession,
    seeds: Iterable[SingleRoleSeed],
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
) -> int:
    created = 0
    for seed in seeds:
        existing = await _find_by_system_key(
            session, IdRoleSingle, tenant_id=tenant_id, system_key=seed.system_key
        )
        if existing is None:
            role = IdRoleSingle(
                tenant_id=tenant_id,
                code=seed.code,
                module=seed.module,
                description=seed.description,
                is_system=True,
                system_key=seed.system_key,
                created_by=actor_principal_id,
                updated_by=actor_principal_id,
            )
            session.add(role)
            await session.flush()
            existing = role
            created += 1

        # Upsert permissions for the role.
        for perm in seed.permissions:
            ao = await _find_auth_object_by_domain(session, tenant_id=tenant_id, domain=perm.domain)
            if ao is None:
                # Should not happen if seeds are internally consistent.
                raise RuntimeError(
                    f"Permission for {seed.code} references unknown "
                    f"auth-object domain {perm.domain!r}"
                )
            await _ensure_permission(
                session,
                tenant_id=tenant_id,
                role_single_id=existing.id,
                auth_object_id=ao.id,
                action_code=perm.action,
                qualifier_values=dict(perm.qualifier_values),
                actor_principal_id=actor_principal_id,
            )
    return created


async def _seed_composite_roles(
    session: AsyncSession,
    seeds: Iterable[CompositeRoleSeed],
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
) -> int:
    created = 0
    for seed in seeds:
        existing = await _find_by_system_key(
            session, IdRoleComposite, tenant_id=tenant_id, system_key=seed.system_key
        )
        if existing is None:
            comp = IdRoleComposite(
                tenant_id=tenant_id,
                code=seed.code,
                description=seed.description,
                is_system=True,
                system_key=seed.system_key,
                created_by=actor_principal_id,
                updated_by=actor_principal_id,
            )
            session.add(comp)
            await session.flush()
            existing = comp
            created += 1

        # Upsert membership.
        for member_code in seed.members:
            single = await _find_single_role_by_code(session, tenant_id=tenant_id, code=member_code)
            if single is None:
                raise RuntimeError(
                    f"Composite role {seed.code} references unknown single role {member_code!r}"
                )
            await _ensure_composite_member(
                session,
                tenant_id=tenant_id,
                composite_id=existing.id,
                single_id=single.id,
                actor_principal_id=actor_principal_id,
            )
    return created


async def _seed_sod_rules(
    session: AsyncSession,
    seeds: Iterable[SodRuleSeed],
    *,
    tenant_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
) -> int:
    created = 0
    for seed in seeds:
        existing = await _find_by_system_key(
            session, IdSodRule, tenant_id=tenant_id, system_key=seed.system_key
        )
        if existing is None:
            rule = IdSodRule(
                tenant_id=tenant_id,
                code=seed.code,
                description=seed.description,
                severity=seed.severity,
                is_system=True,
                system_key=seed.system_key,
                created_by=actor_principal_id,
                updated_by=actor_principal_id,
            )
            session.add(rule)
            await session.flush()
            existing = rule
            created += 1

        for domain, action_code in seed.clauses:
            ao = await _find_auth_object_by_domain(session, tenant_id=tenant_id, domain=domain)
            if ao is None:
                raise RuntimeError(
                    f"SoD rule {seed.code} references unknown auth-object domain {domain!r}"
                )
            await _ensure_sod_clause(
                session,
                tenant_id=tenant_id,
                sod_rule_id=existing.id,
                auth_object_id=ao.id,
                action_code=action_code,
                actor_principal_id=actor_principal_id,
            )
    return created


# ---------------------------------------------------------------------------
# Helper queries (idempotent ensure_* + lookup helpers)
# ---------------------------------------------------------------------------


async def _find_by_system_key[T](
    session: AsyncSession,
    model: type[T],
    *,
    tenant_id: uuid.UUID,
    system_key: str,
) -> T | None:
    # All `is_system`-bearing models declare tenant_id + system_key
    # columns; mypy can't infer that from a generic TypeVar, hence the
    # `# type: ignore` here. The runtime resolution is correct because
    # callers only pass model classes that have these columns.
    stmt = select(model).where(
        model.tenant_id == tenant_id,  # type: ignore[attr-defined]
        model.system_key == system_key,  # type: ignore[attr-defined]
    )
    result: T | None = (await session.execute(stmt)).scalar_one_or_none()
    return result


async def _find_auth_object_by_domain(
    session: AsyncSession, *, tenant_id: uuid.UUID, domain: str
) -> IdAuthObject | None:
    stmt = select(IdAuthObject).where(
        IdAuthObject.tenant_id == tenant_id,
        IdAuthObject.domain == domain,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _find_single_role_by_code(
    session: AsyncSession, *, tenant_id: uuid.UUID, code: str
) -> IdRoleSingle | None:
    stmt = select(IdRoleSingle).where(
        IdRoleSingle.tenant_id == tenant_id, IdRoleSingle.code == code
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _ensure_action(
    session: AsyncSession,
    *,
    auth_object_id: uuid.UUID,
    tenant_id: uuid.UUID,
    action_code: str,
    actor_principal_id: uuid.UUID,
) -> None:
    stmt = select(IdAuthObjectAction).where(
        IdAuthObjectAction.auth_object_id == auth_object_id,
        IdAuthObjectAction.action_code == action_code,
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return
    session.add(
        IdAuthObjectAction(
            tenant_id=tenant_id,
            auth_object_id=auth_object_id,
            action_code=action_code,
            created_by=actor_principal_id,
            updated_by=actor_principal_id,
        )
    )
    await session.flush()


async def _ensure_qualifier(
    session: AsyncSession,
    *,
    auth_object_id: uuid.UUID,
    tenant_id: uuid.UUID,
    qualifier_code: str,
    data_type: str,
    actor_principal_id: uuid.UUID,
) -> None:
    stmt = select(IdAuthObjectQualifier).where(
        IdAuthObjectQualifier.auth_object_id == auth_object_id,
        IdAuthObjectQualifier.qualifier_code == qualifier_code,
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return
    session.add(
        IdAuthObjectQualifier(
            tenant_id=tenant_id,
            auth_object_id=auth_object_id,
            qualifier_code=qualifier_code,
            data_type=data_type,
            created_by=actor_principal_id,
            updated_by=actor_principal_id,
        )
    )
    await session.flush()


async def _ensure_permission(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    role_single_id: uuid.UUID,
    auth_object_id: uuid.UUID,
    action_code: str,
    qualifier_values: dict[str, object],
    actor_principal_id: uuid.UUID,
) -> None:
    stmt = select(IdPermission).where(
        IdPermission.role_single_id == role_single_id,
        IdPermission.auth_object_id == auth_object_id,
        IdPermission.action_code == action_code,
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return
    session.add(
        IdPermission(
            tenant_id=tenant_id,
            role_single_id=role_single_id,
            auth_object_id=auth_object_id,
            action_code=action_code,
            qualifier_values=qualifier_values,
            created_by=actor_principal_id,
            updated_by=actor_principal_id,
        )
    )
    await session.flush()


async def _ensure_composite_member(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    composite_id: uuid.UUID,
    single_id: uuid.UUID,
    actor_principal_id: uuid.UUID,
) -> None:
    stmt = select(IdRoleCompositeMember).where(
        IdRoleCompositeMember.composite_id == composite_id,
        IdRoleCompositeMember.single_id == single_id,
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return
    session.add(
        IdRoleCompositeMember(
            tenant_id=tenant_id,
            composite_id=composite_id,
            single_id=single_id,
            created_by=actor_principal_id,
            updated_by=actor_principal_id,
        )
    )
    await session.flush()


async def _ensure_sod_clause(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    sod_rule_id: uuid.UUID,
    auth_object_id: uuid.UUID,
    action_code: str,
    actor_principal_id: uuid.UUID,
) -> None:
    stmt = select(IdSodRuleClause).where(
        IdSodRuleClause.sod_rule_id == sod_rule_id,
        IdSodRuleClause.auth_object_id == auth_object_id,
        IdSodRuleClause.action_code == action_code,
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return
    session.add(
        IdSodRuleClause(
            tenant_id=tenant_id,
            sod_rule_id=sod_rule_id,
            auth_object_id=auth_object_id,
            action_code=action_code,
            created_by=actor_principal_id,
            updated_by=actor_principal_id,
        )
    )
    await session.flush()


__all__ = ["seed_system_catalogue"]
