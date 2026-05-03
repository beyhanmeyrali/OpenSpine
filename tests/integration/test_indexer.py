"""Integration tests for the embedding indexer + reconciliation.

Exercises the full dual-write loop: MD service publishes →
indexer handler runs in-process → Qdrant collection upserted →
search returns semantic hits.

Then proves recovery: drop the Qdrant collection, run reconciliation,
confirm search works again.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from openspine.config import get_settings
from openspine.core.qdrant import collection_name
from openspine.db import SessionFactory
from openspine.identity import service as identity_service
from openspine.main import app
from openspine.workers.indexer import (
    INDEXED_ENTITIES,
    embed_text,
    get_qdrant_client,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def admin_session() -> AsyncIterator[tuple[AsyncClient, dict[str, str]]]:
    slug = f"idx-{uuid.uuid4().hex[:8]}"
    password = "indexer-pw"
    async with SessionFactory() as db:
        tenant, _admin = await identity_service.bootstrap_tenant_and_admin(
            db,
            tenant_name=f"Indexer test {slug}",
            tenant_slug=slug,
            admin_username="admin",
            admin_display_name="Indexer Admin",
            admin_email=f"admin@{slug}.example",
            admin_password=password,
        )
        await db.commit()
        tenant_id = tenant.id

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            login = await client.post(
                "/auth/login",
                json={"tenant_slug": slug, "username": "admin", "password": password},
            )
            assert login.status_code == 200
            yield client, {"tenant_slug": slug, "tenant_id": str(tenant_id)}

    # Cleanup: drop Qdrant collection + DB rows.
    import contextlib

    qclient = get_qdrant_client(get_settings().qdrant_url)
    coll = collection_name(str(tenant_id))
    with contextlib.suppress(Exception):  # pragma: no cover  (collection may not exist)
        await qclient.delete_collection(collection_name=coll)

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        for table in (
            "fin_document_line",
            "fin_document_header",
            "fin_document_type",
            "fin_ledger",
            "co_cost_centre",
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


# ---------------------------------------------------------------------------
# Embedding fallback contract
# ---------------------------------------------------------------------------


async def test_embed_text_is_deterministic_via_fallback() -> None:
    """The deterministic fallback returns the same vector for the same
    input across calls. Required for the search-by-same-text round-trip
    that v0.1 demos when the embedding provider isn't reachable."""
    a = await embed_text("hello world", ollama_url="http://nonexistent:9999", model="x")
    b = await embed_text("hello world", ollama_url="http://nonexistent:9999", model="x")
    assert a == b
    # Must match Qwen3-Embedding-0.6B native dimension so the same
    # Qdrant collection works whether the real model is available or
    # the fallback is in use.
    assert len(a) == 1024


# ---------------------------------------------------------------------------
# End-to-end: BP create → indexer → search
# ---------------------------------------------------------------------------


async def test_create_bp_indexes_and_search_returns_semantic_hit(
    admin_session: tuple[AsyncClient, dict[str, str]],
) -> None:
    client, _ids = admin_session
    bp = await client.post(
        "/md/business-partners",
        json={
            "number": "V-IDX-001",
            "kind": "organisation",
            "name": "Acme Stainless Imports",
            "country_code": "DE",
            "roles": ["vendor"],
        },
    )
    assert bp.status_code == 201

    # Search by the indexable_text the publisher used (name + number + country).
    response = await client.get(
        "/md/search",
        params={
            "q": "Acme Stainless Imports V-IDX-001 DE",
            "entity": "business_partner",
        },
    )
    body = response.json()
    assert body["hits"], body
    assert any(h["number"] == "V-IDX-001" for h in body["hits"])


async def test_reconcile_rebuilds_after_qdrant_collection_drop(
    admin_session: tuple[AsyncClient, dict[str, str]],
) -> None:
    """The v0.1 §3 #4 acceptance criterion: drop Qdrant, reconcile,
    search works again."""
    client, ids = admin_session
    # Seed BP + material.
    bp = await client.post(
        "/md/business-partners",
        json={
            "number": "V-RECON-001",
            "kind": "organisation",
            "name": "Reconcile Test Vendor",
            "country_code": "DE",
            "roles": ["vendor"],
        },
    )
    assert bp.status_code == 201

    uoms = (await client.get("/md/uoms")).json()
    each = next(u for u in uoms if u["code"] == "EA")
    mat = await client.post(
        "/md/materials",
        json={
            "number": "MAT-RECON-001",
            "description": "Recon Test Material",
            "material_type": "ROH",
            "industry_sector": "M",
            "base_uom_id": each["id"],
        },
    )
    assert mat.status_code == 201

    # Drop the Qdrant collection.
    qclient = get_qdrant_client(get_settings().qdrant_url)
    coll = collection_name(ids["tenant_id"])
    await qclient.delete_collection(collection_name=coll)

    # Search now goes via the structured fallback (Qdrant collection
    # gone → semantic returns empty).
    pre_recon = await client.get(
        "/md/search",
        params={"q": "Reconcile Test Vendor", "entity": "business_partner"},
    )
    assert pre_recon.status_code == 200
    # Structured ILIKE still finds it — the contract holds even
    # when Qdrant is wiped.
    assert any(h["number"] == "V-RECON-001" for h in pre_recon.json()["hits"])

    # Reconcile.
    recon = await client.post("/system/reconcile-embeddings")
    assert recon.status_code == 200, recon.text
    body = recon.json()
    assert body["indexed"]["business_partner"] >= 1
    assert body["indexed"]["material"] >= 1

    # Now Qdrant has the vectors again — search returns semantic hits.
    post_recon = await client.get(
        "/md/search",
        params={"q": "Reconcile Test Vendor V-RECON-001 DE", "entity": "business_partner"},
    )
    assert post_recon.status_code == 200
    body2 = post_recon.json()
    assert any(h["number"] == "V-RECON-001" for h in body2["hits"])


async def test_indexed_entities_are_well_known() -> None:
    """Sanity: the registry of indexed entities matches the search
    endpoint's allowlist. Drift here would cause silent search misses."""
    from openspine.md.router import _SEARCHABLE_ENTITIES

    assert set(INDEXED_ENTITIES) == set(_SEARCHABLE_ENTITIES)
