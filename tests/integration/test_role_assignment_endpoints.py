"""End-to-end test of /auth/principals/{id}/roles assign + revoke.

Demonstrates the full request lifecycle through `@requires_auth`-style
enforcement: bootstrap admin holds SYSTEM_TENANT_ADMIN (and therefore
ROLE_ASSIGN); a non-admin principal does not, and is denied.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from openspine.db import SessionFactory
from openspine.identity import service
from openspine.identity.models import IdCredential, IdPrincipal
from openspine.identity.rbac_models import IdRoleSingle
from openspine.identity.security import hash_password
from openspine.main import app

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def fixture_setup() -> AsyncIterator[dict[str, str]]:
    """Bootstrap a tenant + admin + a non-admin user (with login password)."""
    slug = f"roleep-{uuid.uuid4().hex[:8]}"
    admin_password = "admin-secret"
    user_password = "user-secret"
    async with SessionFactory() as db:
        tenant, admin = await service.bootstrap_tenant_and_admin(
            db,
            tenant_name=f"Role Endpoint {slug}",
            tenant_slug=slug,
            admin_username="admin",
            admin_display_name="Role Admin",
            admin_email=f"admin@{slug}.example",
            admin_password=admin_password,
        )
        await db.commit()

        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant.id))
        )
        # Create a regular principal with a password but no roles.
        regular = IdPrincipal(
            tenant_id=tenant.id,
            kind="human",
            username="regular",
            display_name="Regular User",
            status="active",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add(regular)
        await db.flush()
        db.add(
            IdCredential(
                tenant_id=tenant.id,
                principal_id=regular.id,
                kind="password",
                secret_hash=hash_password(user_password),
                status="active",
                created_by=admin.id,
                updated_by=admin.id,
            )
        )
        await db.commit()
        ids = {
            "tenant_id": str(tenant.id),
            "tenant_slug": slug,
            "admin_id": str(admin.id),
            "admin_password": admin_password,
            "regular_id": str(regular.id),
            "user_password": user_password,
        }
        tenant_id = tenant.id

    yield ids

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        for table in (
            "id_auth_decision_log",
            "id_sod_override",
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


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def _login(client: AsyncClient, slug: str, username: str, password: str) -> None:
    response = await client.post(
        "/auth/login",
        json={"tenant_slug": slug, "username": username, "password": password},
    )
    assert response.status_code == 200, response.text


async def test_admin_can_assign_role_to_principal(
    http_client: AsyncClient, fixture_setup: dict[str, str]
) -> None:
    await _login(
        http_client,
        fixture_setup["tenant_slug"],
        "admin",
        fixture_setup["admin_password"],
    )

    # Pick a single role to assign.
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=fixture_setup["tenant_id"]
            )
        )
        role = (
            await db.execute(
                select(IdRoleSingle).where(
                    IdRoleSingle.tenant_id == uuid.UUID(fixture_setup["tenant_id"]),
                    IdRoleSingle.code == "USER_CREATE",
                )
            )
        ).scalar_one()
        role_id = role.id

    response = await http_client.post(
        f"/auth/principals/{fixture_setup['regular_id']}/roles",
        json={"role_single_id": str(role_id)},
    )
    assert response.status_code == 201, response.text
    binding_id = response.json()["binding_id"]
    assert uuid.UUID(binding_id)


async def test_non_admin_user_is_denied_role_assignment(
    http_client: AsyncClient, fixture_setup: dict[str, str]
) -> None:
    """The regular user (no roles) cannot assign roles."""
    await _login(
        http_client,
        fixture_setup["tenant_slug"],
        "regular",
        fixture_setup["user_password"],
    )
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=fixture_setup["tenant_id"]
            )
        )
        role = (
            await db.execute(
                select(IdRoleSingle).where(
                    IdRoleSingle.tenant_id == uuid.UUID(fixture_setup["tenant_id"]),
                    IdRoleSingle.code == "USER_CREATE",
                )
            )
        ).scalar_one()
        role_id = role.id

    response = await http_client.post(
        f"/auth/principals/{fixture_setup['regular_id']}/roles",
        json={"role_single_id": str(role_id)},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error"] == "authorisation_denied"
    assert body["domain"] == "system.role"
    assert body["action"] == "assign"


async def test_admin_revoke_round_trip(
    http_client: AsyncClient, fixture_setup: dict[str, str]
) -> None:
    await _login(
        http_client,
        fixture_setup["tenant_slug"],
        "admin",
        fixture_setup["admin_password"],
    )

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=fixture_setup["tenant_id"]
            )
        )
        role = (
            await db.execute(
                select(IdRoleSingle).where(
                    IdRoleSingle.tenant_id == uuid.UUID(fixture_setup["tenant_id"]),
                    IdRoleSingle.code == "TOKEN_REVOKE",
                )
            )
        ).scalar_one()
        role_id = role.id

    assigned = await http_client.post(
        f"/auth/principals/{fixture_setup['regular_id']}/roles",
        json={"role_single_id": str(role_id)},
    )
    binding_id = assigned.json()["binding_id"]

    revoke = await http_client.delete(
        f"/auth/principals/{fixture_setup['regular_id']}/roles/{binding_id}"
    )
    assert revoke.status_code == 204

    # Revoking again returns 404 (binding gone).
    again = await http_client.delete(
        f"/auth/principals/{fixture_setup['regular_id']}/roles/{binding_id}"
    )
    assert again.status_code == 404


async def test_assign_validates_exactly_one_role_kind(
    http_client: AsyncClient, fixture_setup: dict[str, str]
) -> None:
    await _login(
        http_client,
        fixture_setup["tenant_slug"],
        "admin",
        fixture_setup["admin_password"],
    )
    response = await http_client.post(
        f"/auth/principals/{fixture_setup['regular_id']}/roles",
        json={},
    )
    assert response.status_code == 422
    assert response.json()["reason"] == "exactly_one_role_kind_required"
