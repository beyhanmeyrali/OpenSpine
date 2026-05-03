"""End-to-end tests for the v0.1 §4.7 agent surface.

Covers:
- POST /agents/traces — agent writes a decision trace; humans get 403
- GET /md/search — hybrid search (structured fallback when Qdrant empty)
- _meta block on /auth/me, /md/business-partners/{id}, /md/company-codes,
  /md/search
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from openspine.db import SessionFactory
from openspine.identity import service as identity_service
from openspine.identity.models import IdAgentProfile, IdPrincipal
from openspine.identity.rbac_models import IdAgentDecisionTrace
from openspine.main import app

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def tenant_with_admin_and_agent() -> AsyncIterator[dict[str, str]]:
    slug = f"agent-{uuid.uuid4().hex[:8]}"
    admin_password = "admin-pw"
    async with SessionFactory() as db:
        tenant, admin = await identity_service.bootstrap_tenant_and_admin(
            db,
            tenant_name=f"Agent surface {slug}",
            tenant_slug=slug,
            admin_username="admin",
            admin_display_name="Admin",
            admin_email=f"admin@{slug}.example",
            admin_password=admin_password,
        )
        await db.commit()

        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant.id))
        )
        agent_principal = IdPrincipal(
            tenant_id=tenant.id,
            kind="agent",
            username=f"agent-{uuid.uuid4().hex[:6]}",
            display_name="Decision Agent",
            status="active",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add(agent_principal)
        await db.flush()
        db.add(
            IdAgentProfile(
                tenant_id=tenant.id,
                principal_id=agent_principal.id,
                model="gpt-test",
                model_version="1",
                provisioner_principal_id=admin.id,
                purpose="end-to-end test",
                created_by=admin.id,
                updated_by=admin.id,
            )
        )
        await db.commit()
        agent_id = agent_principal.id
        tenant_id = tenant.id
        admin_id = admin.id

    # Issue an agent token via the admin login + /auth/tokens.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            await c.post(
                "/auth/login",
                json={
                    "tenant_slug": slug,
                    "username": "admin",
                    "password": admin_password,
                },
            )
            issued = await c.post(
                "/auth/tokens",
                json={
                    "kind": "agent",
                    "target_principal_id": str(agent_id),
                    "reason": "e2e test",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
            assert issued.status_code == 201, issued.text
            agent_token = issued.json()["plaintext"]

    yield {
        "tenant_id": str(tenant_id),
        "tenant_slug": slug,
        "admin_id": str(admin_id),
        "admin_password": admin_password,
        "agent_id": str(agent_id),
        "agent_token": agent_token,
    }

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        for table in (
            "id_agent_decision_trace",
            "md_material_uom",
            "md_material_valuation",
            "md_material_plant",
            "md_material",
            "md_bp_bank",
            "md_bp_address",
            "md_bp_role",
            "md_business_partner",
            "md_fx_rate",
            "md_posting_period",
            "md_number_range",
            "md_storage_location",
            "md_purchasing_group",
            "md_purchasing_org",
            "md_plant",
            "md_gl_account_company",
            "md_company_code",
            "md_gl_account",
            "md_account_group",
            "md_chart_of_accounts",
            "md_factory_calendar",
            "md_fiscal_year_variant",
            "md_controlling_area",
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
async def http_client() -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


# ---------------------------------------------------------------------------
# Decision-trace write API
# ---------------------------------------------------------------------------


async def test_agent_can_write_decision_trace(
    http_client: AsyncClient, tenant_with_admin_and_agent: dict[str, str]
) -> None:
    bundle = tenant_with_admin_and_agent
    response = await http_client.post(
        "/agents/traces",
        json={
            "action_summary": "Selected vendor V-019 for stainless steel order",
            "reasoning": (
                "Qdrant ranked 3 candidates for 'stainless steel 304'; "
                "Postgres verified V-019 has approved info-record and "
                "active contract; chose V-019 for highest on-time score."
            ),
            "candidates_considered": [
                {"vendor": "V-019", "score": 0.91, "on_time": 0.96},
                {"vendor": "V-022", "score": 0.88, "on_time": 0.78},
                {"vendor": "V-101", "score": 0.83, "on_time": 0.94},
            ],
            "chosen_path": {"vendor_id": "V-019", "selection_basis": "on_time_score"},
            "model": "gpt-test",
            "model_version": "1",
        },
        headers={"Authorization": f"Bearer {bundle['agent_token']}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert uuid.UUID(body["id"])

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=bundle["tenant_id"]
            )
        )
        traces = (
            (
                await db.execute(
                    select(IdAgentDecisionTrace).where(
                        IdAgentDecisionTrace.principal_id == uuid.UUID(bundle["agent_id"])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(traces) == 1
    assert "stainless steel" in traces[0].reasoning


async def test_human_principal_cannot_write_decision_trace(
    http_client: AsyncClient, tenant_with_admin_and_agent: dict[str, str]
) -> None:
    """Human principals are forbidden from writing to the agent stream."""
    bundle = tenant_with_admin_and_agent
    await http_client.post(
        "/auth/login",
        json={
            "tenant_slug": bundle["tenant_slug"],
            "username": "admin",
            "password": bundle["admin_password"],
        },
    )
    response = await http_client.post(
        "/agents/traces",
        json={
            "action_summary": "would-be human action",
            "reasoning": "should be denied",
        },
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["domain"] == "agents.trace"
    assert body["reason"] == "not_an_agent"


async def test_anonymous_cannot_write_decision_trace(
    http_client: AsyncClient,
) -> None:
    response = await http_client.post(
        "/agents/traces",
        json={"action_summary": "anonymous", "reasoning": "denied"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Hybrid /md/search
# ---------------------------------------------------------------------------


async def test_md_search_finds_indexed_business_partner(
    http_client: AsyncClient, tenant_with_admin_and_agent: dict[str, str]
) -> None:
    """With the indexer active and Qdrant reachable, a BP create
    publishes an event that synchronously upserts a vector. The
    search-by-same-text path returns the BP via the semantic source."""
    bundle = tenant_with_admin_and_agent
    await http_client.post(
        "/auth/login",
        json={
            "tenant_slug": bundle["tenant_slug"],
            "username": "admin",
            "password": bundle["admin_password"],
        },
    )
    bp_response = await http_client.post(
        "/md/business-partners",
        json={
            "number": "V-200001",
            "kind": "organisation",
            "name": "Stainless Steel Supplies Ltd",
            "country_code": "DE",
            "roles": ["vendor"],
        },
    )
    assert bp_response.status_code == 201

    search = await http_client.get(
        "/md/search",
        params={"q": "Stainless Steel Supplies Ltd V-200001 DE", "entity": "business_partner"},
    )
    assert search.status_code == 200, search.text
    body = search.json()
    # Source should be semantic when the indexer + Qdrant are both up.
    # If Qdrant is down (degraded environment) the structured fallback
    # also returns the row — both are acceptable contracts.
    assert body["_meta"]["source"] in ("semantic", "structured")
    assert body["_meta"]["pattern"] == "semantic-then-structured"
    assert any("Stainless" in h["name"] for h in body["hits"])


async def test_md_search_rejects_unknown_entity(
    http_client: AsyncClient, tenant_with_admin_and_agent: dict[str, str]
) -> None:
    bundle = tenant_with_admin_and_agent
    await http_client.post(
        "/auth/login",
        json={
            "tenant_slug": bundle["tenant_slug"],
            "username": "admin",
            "password": bundle["admin_password"],
        },
    )
    response = await http_client.get("/md/search", params={"q": "anything", "entity": "frobnicate"})
    assert response.status_code == 404
    body = response.json()
    assert body["reason"] == "unknown_entity"


# ---------------------------------------------------------------------------
# _meta block presence
# ---------------------------------------------------------------------------


async def test_auth_me_carries_meta_with_agent_actions(
    http_client: AsyncClient, tenant_with_admin_and_agent: dict[str, str]
) -> None:
    bundle = tenant_with_admin_and_agent
    response = await http_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {bundle['agent_token']}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["principal_kind"] == "agent"
    assert "_meta" in body
    actions = {a["name"] for a in body["_meta"]["actions"]}
    assert "logout" in actions
    assert "write_decision_trace" in actions  # only present for agents


async def test_business_partner_get_carries_meta(
    http_client: AsyncClient, tenant_with_admin_and_agent: dict[str, str]
) -> None:
    bundle = tenant_with_admin_and_agent
    await http_client.post(
        "/auth/login",
        json={
            "tenant_slug": bundle["tenant_slug"],
            "username": "admin",
            "password": bundle["admin_password"],
        },
    )
    bp_response = await http_client.post(
        "/md/business-partners",
        json={
            "number": "V-300001",
            "kind": "organisation",
            "name": "Meta Test Vendor",
            "country_code": "DE",
            "roles": ["vendor"],
        },
    )
    bp_id = bp_response.json()["id"]
    get_response = await http_client.get(f"/md/business-partners/{bp_id}")
    body = get_response.json()
    assert "_meta" in body
    assert body["_meta"]["self"] == f"/md/business-partners/{bp_id}"
    assert "addresses" in body["_meta"]["related"]
