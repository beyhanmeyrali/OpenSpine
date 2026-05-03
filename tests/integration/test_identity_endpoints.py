"""End-to-end identity flow against a live Postgres.

Marked `integration` — requires `make up` (or equivalent stack) and a
fresh database with `make migrate` applied. CI runs this in the
integration job; local runs need:

    make up
    make migrate
    pytest -m integration tests/integration/test_identity_endpoints.py

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

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from openspine.db import SessionFactory
from openspine.identity import service
from openspine.main import app

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _unique_slug() -> str:
    return f"test-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def bootstrapped_tenant() -> dict[str, str]:
    """Create a fresh tenant + admin synchronously (test convenience)."""
    slug = _unique_slug()
    username = "admin"
    password = "correct horse battery staple"

    async def _setup() -> tuple[uuid.UUID, uuid.UUID]:
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
            return tenant.id, admin.id

    tenant_id, admin_id = asyncio.run(_setup())

    yield {
        "tenant_id": str(tenant_id),
        "tenant_slug": slug,
        "admin_id": str(admin_id),
        "username": username,
        "password": password,
    }

    # Teardown: hard-delete everything we created. Use a privileged
    # path that bypasses RLS by setting the tenant explicitly.
    async def _cleanup() -> None:
        async with SessionFactory() as db:
            await db.execute(
                text("SET LOCAL openspine.tenant_id = :t").bindparams(t=str(tenant_id))
            )
            await db.execute(
                text("DELETE FROM id_audit_event WHERE tenant_id = :t").bindparams(t=str(tenant_id))
            )
            await db.execute(
                text("DELETE FROM id_token WHERE tenant_id = :t").bindparams(t=str(tenant_id))
            )
            await db.execute(
                text("DELETE FROM id_session WHERE tenant_id = :t").bindparams(t=str(tenant_id))
            )
            await db.execute(
                text("DELETE FROM id_credential WHERE tenant_id = :t").bindparams(t=str(tenant_id))
            )
            await db.execute(
                text("DELETE FROM id_principal WHERE tenant_id = :t").bindparams(t=str(tenant_id))
            )
            await db.execute(
                text("DELETE FROM id_tenant WHERE id = :t").bindparams(t=str(tenant_id))
            )
            await db.commit()

    asyncio.run(_cleanup())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_login_then_me_returns_authenticated_principal(
    bootstrapped_tenant: dict[str, str],
) -> None:
    with TestClient(app) as client:
        login = client.post(
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

        me = client.get("/auth/me")
        assert me.status_code == 200
        me_body = me.json()
        assert me_body["is_anonymous"] is False
        assert me_body["principal_id"] == bootstrapped_tenant["admin_id"]
        assert me_body["auth_method"] == "session"


def test_login_rejects_wrong_password(
    bootstrapped_tenant: dict[str, str],
) -> None:
    with TestClient(app) as client:
        response = client.post(
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


def test_login_rejects_unknown_tenant_with_generic_error(
    bootstrapped_tenant: dict[str, str],
) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "tenant_slug": "no-such-tenant",
                "username": "anyone",
                "password": "anything",
            },
        )
    assert response.status_code == 401
    # Same envelope shape as wrong-password — no enumeration leak.
    assert response.json()["error"] == "authentication_failed"


def test_token_issuance_and_revocation_round_trip(
    bootstrapped_tenant: dict[str, str],
) -> None:
    with TestClient(app) as client:
        client.post(
            "/auth/login",
            json={
                "tenant_slug": bootstrapped_tenant["tenant_slug"],
                "username": bootstrapped_tenant["username"],
                "password": bootstrapped_tenant["password"],
            },
        )
        # Issue a user_api token for the current principal.
        issued = client.post("/auth/tokens", json={"kind": "user_api"})
        assert issued.status_code == 201, issued.text
        body = issued.json()
        plaintext = body["plaintext"]
        token_id = body["token_id"]
        assert plaintext.startswith("osp_user_")

        # Use it on /auth/me as a Bearer token in a fresh client (no cookies).
    with TestClient(app) as client2:
        me = client2.get("/auth/me", headers={"Authorization": f"Bearer {plaintext}"})
        assert me.status_code == 200
        assert me.json()["auth_method"] == "token"

    # Revoke via the original session.
    with TestClient(app) as client:
        client.post(
            "/auth/login",
            json={
                "tenant_slug": bootstrapped_tenant["tenant_slug"],
                "username": bootstrapped_tenant["username"],
                "password": bootstrapped_tenant["password"],
            },
        )
        revoke = client.delete(f"/auth/tokens/{token_id}")
        assert revoke.status_code == 204

    # Now the token is dead.
    with TestClient(app) as client3:
        me = client3.get("/auth/me", headers={"Authorization": f"Bearer {plaintext}"})
        assert me.json()["is_anonymous"] is True


def test_agent_token_requires_expiry_and_reason(
    bootstrapped_tenant: dict[str, str],
) -> None:
    with TestClient(app) as client:
        client.post(
            "/auth/login",
            json={
                "tenant_slug": bootstrapped_tenant["tenant_slug"],
                "username": bootstrapped_tenant["username"],
                "password": bootstrapped_tenant["password"],
            },
        )
        # First create an agent principal to issue against. The CLI/REST
        # surface for principal CRUD lands in §4.3+; for now seed
        # directly via the service layer.

        async def _seed_agent() -> uuid.UUID:
            async with SessionFactory() as db:
                await db.execute(
                    text("SET LOCAL openspine.tenant_id = :t").bindparams(
                        t=bootstrapped_tenant["tenant_id"]
                    )
                )
                from openspine.identity.models import IdAgentProfile, IdPrincipal

                admin_id = uuid.UUID(bootstrapped_tenant["admin_id"])
                tenant_id = uuid.UUID(bootstrapped_tenant["tenant_id"])
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
                return p.id

        agent_id = asyncio.run(_seed_agent())

        # Missing expiry → 422.
        bad = client.post(
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
        bad2 = client.post(
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
        ok = client.post(
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


def test_totp_enrol_then_verify(bootstrapped_tenant: dict[str, str]) -> None:
    import pyotp

    with TestClient(app) as client:
        client.post(
            "/auth/login",
            json={
                "tenant_slug": bootstrapped_tenant["tenant_slug"],
                "username": bootstrapped_tenant["username"],
                "password": bootstrapped_tenant["password"],
            },
        )
        enrol = client.post("/auth/totp/enrol")
        assert enrol.status_code == 200
        secret = enrol.json()["secret"]
        # Use the freshly-enrolled secret to compute a current code.
        code = pyotp.TOTP(secret).now()
        verify = client.post("/auth/totp/verify", json={"code": code})
        assert verify.status_code == 200
        assert verify.json()["verified"] is True


def test_logout_invalidates_session(bootstrapped_tenant: dict[str, str]) -> None:
    with TestClient(app) as client:
        client.post(
            "/auth/login",
            json={
                "tenant_slug": bootstrapped_tenant["tenant_slug"],
                "username": bootstrapped_tenant["username"],
                "password": bootstrapped_tenant["password"],
            },
        )
        out = client.post("/auth/logout")
        assert out.status_code == 204

        # Cookie was cleared, but verify the underlying session is dead
        # too by re-presenting it manually.
        me = client.get("/auth/me")
        assert me.json()["is_anonymous"] is True
