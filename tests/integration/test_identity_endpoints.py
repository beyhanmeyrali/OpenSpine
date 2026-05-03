"""End-to-end identity flow against a live Postgres.

Marked `integration` — requires `make up` (or equivalent stack) and a
fresh database with `make migrate` applied. CI runs this in the
integration job; local runs need:

    make up
    make migrate
    pytest -m integration tests/integration/test_identity_endpoints.py

The tests are async with `httpx.AsyncClient + ASGITransport` so that
the asyncpg engine, the fixture setup/teardown, and the request
handlers all share one event loop. Sync `TestClient` opens its own
loop per request, which conflicts with the module-level engine's
connection pool (asyncpg connections cannot survive their original
loop being closed).

Coverage:
- Bootstrap a tenant + admin via the service layer.
- Login with that admin → cookie issued.
- /auth/me with the cookie → not anonymous, correct principal.
- Issue a user_api token, then call an authenticated endpoint with it.
- Revoke the token; subsequent use is rejected.
- Agent-token CHECK constraint blocks tokens with no expires_at.
- TOTP enrol + verify round-trip.
- Logout invalidates the session.
- Wrong-password / wrong-tenant attempts return generic errors.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from openspine.db import SessionFactory
from openspine.identity import service
from openspine.identity.models import IdAgentProfile, IdPrincipal
from openspine.main import app

pytestmark = pytest.mark.integration


def _unique_slug() -> str:
    return f"test-{uuid.uuid4().hex[:10]}"


@pytest_asyncio.fixture
async def bootstrapped_tenant() -> AsyncIterator[dict[str, str]]:
    """Create a fresh tenant + admin, yield identifiers, clean up after."""
    slug = _unique_slug()
    username = "admin"
    password = "correct horse battery staple"

    async with SessionFactory() as db:
        tenant, admin = await service.bootstrap_tenant_and_admin(
            db,
            tenant_name=f"Test {slug}",
            tenant_slug=slug,
            admin_username=username,
            admin_display_name="Test Admin",
            admin_email=f"admin@{slug}.example",
            admin_password=password,
        )
        await db.commit()
        tenant_id = tenant.id
        admin_id = admin.id

    yield {
        "tenant_id": str(tenant_id),
        "tenant_slug": slug,
        "admin_id": str(admin_id),
        "username": username,
        "password": password,
    }

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        # Explicit ::uuid casts because asyncpg sends bound strings as
        # VARCHAR; Postgres won't implicitly compare uuid = varchar.
        for table in (
            "id_audit_event",
            "id_token",
            "id_session",
            "id_credential",
            "id_human_profile",
            "id_agent_profile",
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
async def client() -> AsyncIterator[AsyncClient]:
    """ASGI-transport client that shares the asyncio loop with our DB engine."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_login_then_me_returns_authenticated_principal(
    client: AsyncClient, bootstrapped_tenant: dict[str, str]
) -> None:
    login = await client.post(
        "/auth/login",
        json={
            "tenant_slug": bootstrapped_tenant["tenant_slug"],
            "username": bootstrapped_tenant["username"],
            "password": bootstrapped_tenant["password"],
        },
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["principal_id"] == bootstrapped_tenant["admin_id"]
    assert body["requires_totp"] is False

    me = await client.get("/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["is_anonymous"] is False
    assert me_body["principal_id"] == bootstrapped_tenant["admin_id"]
    assert me_body["auth_method"] == "session"


async def test_login_rejects_wrong_password(
    client: AsyncClient, bootstrapped_tenant: dict[str, str]
) -> None:
    response = await client.post(
        "/auth/login",
        json={
            "tenant_slug": bootstrapped_tenant["tenant_slug"],
            "username": bootstrapped_tenant["username"],
            "password": "definitely wrong",
        },
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "authentication_failed"
    assert body["reason"] == "invalid_credentials"


async def test_login_rejects_unknown_tenant_with_generic_error(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/auth/login",
        json={
            "tenant_slug": "no-such-tenant",
            "username": "anyone",
            "password": "anything",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"] == "authentication_failed"


async def test_token_issuance_and_revocation_round_trip(
    client: AsyncClient, bootstrapped_tenant: dict[str, str]
) -> None:
    await client.post(
        "/auth/login",
        json={
            "tenant_slug": bootstrapped_tenant["tenant_slug"],
            "username": bootstrapped_tenant["username"],
            "password": bootstrapped_tenant["password"],
        },
    )
    issued = await client.post("/auth/tokens", json={"kind": "user_api"})
    assert issued.status_code == 201, issued.text
    body = issued.json()
    plaintext = body["plaintext"]
    token_id = body["token_id"]
    assert plaintext.startswith("osp_user_")

    # Use the token from a fresh client (no cookies).
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c2:
        me = await c2.get("/auth/me", headers={"Authorization": f"Bearer {plaintext}"})
        assert me.status_code == 200
        assert me.json()["auth_method"] == "token"

    # Revoke from the original session.
    revoke = await client.delete(f"/auth/tokens/{token_id}")
    assert revoke.status_code == 204

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c3:
        me = await c3.get("/auth/me", headers={"Authorization": f"Bearer {plaintext}"})
        assert me.json()["is_anonymous"] is True


async def test_agent_token_requires_expiry_and_reason(
    client: AsyncClient, bootstrapped_tenant: dict[str, str]
) -> None:
    await client.post(
        "/auth/login",
        json={
            "tenant_slug": bootstrapped_tenant["tenant_slug"],
            "username": bootstrapped_tenant["username"],
            "password": bootstrapped_tenant["password"],
        },
    )
    # Seed an agent principal directly (admin-CRUD endpoints land in §4.3+).
    admin_id = uuid.UUID(bootstrapped_tenant["admin_id"])
    tenant_id = uuid.UUID(bootstrapped_tenant["tenant_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        p = IdPrincipal(
            tenant_id=tenant_id,
            kind="agent",
            username=f"agent-{uuid.uuid4().hex[:6]}",
            display_name="Test Agent",
            status="active",
            created_by=admin_id,
            updated_by=admin_id,
        )
        db.add(p)
        await db.flush()
        profile = IdAgentProfile(
            tenant_id=tenant_id,
            principal_id=p.id,
            model="gpt-test",
            model_version="1",
            provisioner_principal_id=admin_id,
            purpose="integration test",
            created_by=admin_id,
            updated_by=admin_id,
        )
        db.add(profile)
        await db.commit()
        agent_id = p.id

    # Missing expiry → 422.
    bad = await client.post(
        "/auth/tokens",
        json={
            "kind": "agent",
            "target_principal_id": str(agent_id),
            "reason": "automation",
        },
    )
    assert bad.status_code == 422
    assert bad.json()["reason"] == "agent_expiry_required"

    # Missing reason → 422.
    bad2 = await client.post(
        "/auth/tokens",
        json={
            "kind": "agent",
            "target_principal_id": str(agent_id),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert bad2.status_code == 422
    assert bad2.json()["reason"] == "agent_reason_required"

    # Both present → 201.
    ok = await client.post(
        "/auth/tokens",
        json={
            "kind": "agent",
            "target_principal_id": str(agent_id),
            "reason": "automated reconciliation",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "scope": {"actions": ["md.material.read"]},
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["plaintext"].startswith("osp_agent_")


async def test_totp_enrol_then_verify(
    client: AsyncClient, bootstrapped_tenant: dict[str, str]
) -> None:
    await client.post(
        "/auth/login",
        json={
            "tenant_slug": bootstrapped_tenant["tenant_slug"],
            "username": bootstrapped_tenant["username"],
            "password": bootstrapped_tenant["password"],
        },
    )
    enrol = await client.post("/auth/totp/enrol")
    assert enrol.status_code == 200
    secret = enrol.json()["secret"]
    code = pyotp.TOTP(secret).now()
    verify = await client.post("/auth/totp/verify", json={"code": code})
    assert verify.status_code == 200
    assert verify.json()["verified"] is True


async def test_logout_invalidates_session(
    client: AsyncClient, bootstrapped_tenant: dict[str, str]
) -> None:
    await client.post(
        "/auth/login",
        json={
            "tenant_slug": bootstrapped_tenant["tenant_slug"],
            "username": bootstrapped_tenant["username"],
            "password": bootstrapped_tenant["password"],
        },
    )
    out = await client.post("/auth/logout")
    assert out.status_code == 204
    me = await client.get("/auth/me")
    assert me.json()["is_anonymous"] is True
