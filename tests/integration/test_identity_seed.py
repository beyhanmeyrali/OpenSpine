"""Integration tests for the system catalogue seeder.

Verifies that:
- bootstrap_tenant_and_admin seeds the catalogue automatically
- the SYSTEM_TENANT_ADMIN composite role is created and granted to the admin
- re-running the seeder is idempotent (counts return 0 on second invocation)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from openspine.db import SessionFactory
from openspine.identity import service
from openspine.identity.rbac_models import (
    IdAuthObject,
    IdPermission,
    IdPrincipalRole,
    IdRoleComposite,
    IdRoleSingle,
    IdSodRule,
)
from openspine.identity.seed import seed_system_catalogue
from openspine.identity.system_catalogue import (
    AUTH_OBJECTS,
    COMPOSITE_ROLES,
    SINGLE_ROLES,
    SOD_RULES,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def seeded_tenant() -> AsyncIterator[dict[str, str]]:
    slug = f"seedtest-{uuid.uuid4().hex[:8]}"
    async with SessionFactory() as db:
        tenant, admin = await service.bootstrap_tenant_and_admin(
            db,
            tenant_name=f"Seed test {slug}",
            tenant_slug=slug,
            admin_username="admin",
            admin_display_name="Seed Admin",
            admin_email=f"admin@{slug}.example",
            admin_password="seed-test-password",
        )
        await db.commit()
        tenant_id = tenant.id
        admin_id = admin.id

    yield {"tenant_id": str(tenant_id), "admin_id": str(admin_id)}

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        for table in (
            "id_auth_decision_log",
            "id_sod_override",
            "fin_document_line",
            "fin_document_header",
            "fin_document_type",
            "fin_ledger",
            "co_cost_centre",
            "md_number_range",
            "id_sod_rule_clause",
            "id_sod_rule",
            "id_principal_role",
            "id_role_composite_member",
            "id_role_composite",
            "id_permission",
            "id_role_single",
            "id_auth_object_qualifier",
            "id_auth_object_action",
            "id_auth_object",
            "id_audit_event",
            "id_token",
            "id_session",
            "id_credential",
            "id_human_profile",
            "id_principal",
        ):
            await db.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = CAST(:t AS uuid)").bindparams(
                    t=str(tenant_id)
                )
            )
        await db.execute(
            text("DELETE FROM id_tenant WHERE id = CAST(:t AS uuid)").bindparams(t=str(tenant_id))
        )
        await db.commit()


async def test_bootstrap_seeds_full_catalogue(seeded_tenant: dict[str, str]) -> None:
    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        ao_count = (
            await db.execute(select(IdAuthObject).where(IdAuthObject.tenant_id == tenant_id))
        ).all()
        single_count = (
            await db.execute(select(IdRoleSingle).where(IdRoleSingle.tenant_id == tenant_id))
        ).all()
        composite_count = (
            await db.execute(select(IdRoleComposite).where(IdRoleComposite.tenant_id == tenant_id))
        ).all()
        sod_count = (
            await db.execute(select(IdSodRule).where(IdSodRule.tenant_id == tenant_id))
        ).all()

    assert len(ao_count) == len(AUTH_OBJECTS)
    assert len(single_count) == len(SINGLE_ROLES)
    assert len(composite_count) == len(COMPOSITE_ROLES)
    assert len(sod_count) == len(SOD_RULES)


async def test_admin_holds_system_tenant_admin_role(
    seeded_tenant: dict[str, str],
) -> None:
    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])
    admin_id = uuid.UUID(seeded_tenant["admin_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        admin_composite = (
            await db.execute(
                select(IdRoleComposite).where(
                    IdRoleComposite.tenant_id == tenant_id,
                    IdRoleComposite.system_key == "SYSTEM_TENANT_ADMIN",
                )
            )
        ).scalar_one()
        binding = (
            await db.execute(
                select(IdPrincipalRole).where(
                    IdPrincipalRole.tenant_id == tenant_id,
                    IdPrincipalRole.principal_id == admin_id,
                    IdPrincipalRole.role_composite_id == admin_composite.id,
                )
            )
        ).scalar_one_or_none()
    assert binding is not None


async def test_reseeding_is_idempotent(seeded_tenant: dict[str, str]) -> None:
    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])
    admin_id = uuid.UUID(seeded_tenant["admin_id"])

    async with SessionFactory() as db:
        counts = await seed_system_catalogue(db, tenant_id=tenant_id, actor_principal_id=admin_id)
        await db.commit()

    # Already seeded by the bootstrap; second pass should add nothing.
    assert counts == {
        "auth_objects": 0,
        "single_roles": 0,
        "composite_roles": 0,
        "sod_rules": 0,
    }


async def test_known_permissions_exist(seeded_tenant: dict[str, str]) -> None:
    """A spot-check: USER_CREATE single role grants system.user:create."""
    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        user_create = (
            await db.execute(
                select(IdRoleSingle).where(
                    IdRoleSingle.tenant_id == tenant_id,
                    IdRoleSingle.code == "USER_CREATE",
                )
            )
        ).scalar_one()
        ao = (
            await db.execute(
                select(IdAuthObject).where(
                    IdAuthObject.tenant_id == tenant_id,
                    IdAuthObject.domain == "system.user",
                )
            )
        ).scalar_one()
        perm = (
            await db.execute(
                select(IdPermission).where(
                    IdPermission.role_single_id == user_create.id,
                    IdPermission.auth_object_id == ao.id,
                    IdPermission.action_code == "create",
                )
            )
        ).scalar_one_or_none()
    assert perm is not None
