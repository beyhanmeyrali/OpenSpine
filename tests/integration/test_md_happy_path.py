"""v0.1 §3 acceptance happy path — end-to-end MD lifecycle.

Per `docs/roadmap/v0.1-foundation.md` §3 #2:

> Create tenant → create Company Code → create CoA + 3 GL accounts →
>   create vendor BP → create material → upload FX rates →
>   open posting period.
> Every entity is queryable via REST.

This test runs that path through the HTTP surface against a live
Postgres + the bootstrap-seeded admin (who holds SYSTEM_TENANT_ADMIN
+ MD_ADMIN). Tenant + admin come from `bootstrap_tenant_and_admin`;
everything else lands via /md endpoints.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from openspine.db import SessionFactory
from openspine.identity import service as identity_service
from openspine.main import app

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def admin_session() -> AsyncIterator[tuple[AsyncClient, dict[str, str]]]:
    slug = f"mdpath-{uuid.uuid4().hex[:8]}"
    password = "md-admin-password"
    async with SessionFactory() as db:
        tenant, _admin = await identity_service.bootstrap_tenant_and_admin(
            db,
            tenant_name=f"MD Happy {slug}",
            tenant_slug=slug,
            admin_username="admin",
            admin_display_name="MD Admin",
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
            assert login.status_code == 200, login.text
            yield client, {"tenant_slug": slug, "tenant_id": str(tenant_id)}

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        for table in (
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


async def test_v0_1_happy_path_end_to_end(
    admin_session: tuple[AsyncClient, dict[str, str]],
) -> None:
    client, _ids = admin_session

    # 0. Confirm the global currency catalogue is seeded and reachable.
    currencies = (await client.get("/md/currencies")).json()
    eur = next(c for c in currencies if c["code"] == "EUR")
    usd = next(c for c in currencies if c["code"] == "USD")

    uoms = (await client.get("/md/uoms")).json()
    each = next(u for u in uoms if u["code"] == "EA")

    # 1. Fiscal year variant
    fyv = await client.post(
        "/md/fiscal-year-variants",
        json={"code": "K4", "description": "Calendar year, 4 special periods"},
    )
    assert fyv.status_code == 201, fyv.text
    fyv_id = fyv.json()["id"]

    # 2. Chart of Accounts
    coa = await client.post(
        "/md/charts-of-accounts",
        json={"code": "INT", "name": "International CoA"},
    )
    assert coa.status_code == 201, coa.text
    coa_id = coa.json()["id"]

    # 3. Three GL accounts (one balance sheet, two P&L)
    gl_payloads = [
        {
            "chart_of_accounts_id": coa_id,
            "account_number": "100000",
            "name": "Cash on Hand",
            "account_kind": "balance_sheet",
        },
        {
            "chart_of_accounts_id": coa_id,
            "account_number": "400000",
            "name": "Sales Revenue",
            "account_kind": "pnl",
        },
        {
            "chart_of_accounts_id": coa_id,
            "account_number": "500000",
            "name": "Cost of Goods Sold",
            "account_kind": "pnl",
        },
    ]
    gl_ids = []
    for p in gl_payloads:
        r = await client.post("/md/gl-accounts", json=p)
        assert r.status_code == 201, r.text
        gl_ids.append(r.json()["id"])
    assert len(gl_ids) == 3

    # 4. Company Code
    cc = await client.post(
        "/md/company-codes",
        json={
            "code": "DE01",
            "name": "OpenSpine Deutschland GmbH",
            "country_code": "DE",
            "local_currency_id": eur["id"],
            "chart_of_accounts_id": coa_id,
            "fiscal_year_variant_id": fyv_id,
        },
    )
    assert cc.status_code == 201, cc.text
    cc_id = cc.json()["id"]

    # Listing returns it.
    cc_list = (await client.get("/md/company-codes")).json()
    assert any(c["code"] == "DE01" for c in cc_list)

    # 5. Plant under DE01
    plant = await client.post(
        "/md/plants",
        json={"code": "P001", "name": "Berlin Warehouse", "company_code_id": cc_id},
    )
    assert plant.status_code == 201, plant.text
    plant_id = plant.json()["id"]

    # 6. Vendor BP
    bp = await client.post(
        "/md/business-partners",
        json={
            "number": "V-100001",
            "kind": "organisation",
            "name": "Acme Components GmbH",
            "country_code": "DE",
            "roles": ["vendor"],
            "addresses": [
                {
                    "kind": "legal",
                    "line1": "Hauptstr. 1",
                    "city": "München",
                    "postal_code": "80331",
                    "country_code": "DE",
                }
            ],
        },
    )
    assert bp.status_code == 201, bp.text
    bp_id = bp.json()["id"]
    bp_get_resp = await client.get(f"/md/business-partners/{bp_id}")
    assert bp_get_resp.status_code == 200, bp_get_resp.text
    bp_get = bp_get_resp.json()
    assert "vendor" in bp_get["roles"]

    # 7. Material with plant + valuation extensions
    mat = await client.post(
        "/md/materials",
        json={
            "number": "MAT-100001",
            "description": "Stainless Steel 304 Sheet",
            "material_type": "ROH",
            "industry_sector": "M",
            "base_uom_id": each["id"],
        },
    )
    assert mat.status_code == 201, mat.text
    mat_id = mat.json()["id"]

    mp = await client.post(
        "/md/material-plants",
        json={
            "material_id": mat_id,
            "plant_id": plant_id,
            "procurement_type": "F",
            "mrp_type": "PD",
        },
    )
    assert mp.status_code == 201, mp.text

    val = await client.post(
        "/md/material-valuations",
        json={
            "material_id": mat_id,
            "valuation_area_id": plant_id,
            "price_control": "S",
            "currency_id": eur["id"],
            "standard_price": "150.00",
            "valuation_class": "3000",
        },
    )
    assert val.status_code == 201, val.text

    # 8. Upload FX rate USD → EUR
    fx = await client.post(
        "/md/fx-rates",
        json={
            "rate_type": "M",
            "from_currency_id": usd["id"],
            "to_currency_id": eur["id"],
            "valid_from": str(date(2026, 5, 1)),
            "rate": "0.92",
        },
    )
    assert fx.status_code == 201, fx.text

    # 9. Open the first posting period
    period = await client.post(
        "/md/posting-periods",
        json={
            "company_code_id": cc_id,
            "fiscal_year": 2026,
            "period": 5,
            "period_start_date": "2026-05-01",
            "period_end_date": "2026-05-31",
            "state": "open",
        },
    )
    assert period.status_code == 201, period.text
    body = period.json()
    assert body["state"] == "open"
    assert body["period"] == 5

    # 10. Toggle the state — close, reopen.
    close = await client.post(
        f"/md/company-codes/{cc_id}/posting-periods/2026/5/state",
        json={"state": "closed"},
    )
    assert close.status_code == 200
    assert close.json()["state"] == "closed"

    reopen = await client.post(
        f"/md/company-codes/{cc_id}/posting-periods/2026/5/state",
        json={"state": "open"},
    )
    assert reopen.status_code == 200
    assert reopen.json()["state"] == "open"


async def test_md_endpoints_require_authentication() -> None:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/md/currencies")
    assert response.status_code == 401


async def test_md_create_requires_authorisation_role(
    admin_session: tuple[AsyncClient, dict[str, str]],
) -> None:
    """Demonstrate the @requires_auth gate denies a principal who lacks
    the relevant md.* permission."""
    _client_unused, ids = admin_session
    tenant_id = uuid.UUID(ids["tenant_id"])

    # Create a regular human in the tenant with no roles + a password,
    # then login as them and try to create a CoA.
    from openspine.identity.models import IdCredential, IdPrincipal
    from openspine.identity.security import hash_password

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        # The bootstrap admin's id will be the created_by — find it.
        from sqlalchemy import select

        admin = (
            await db.execute(
                select(IdPrincipal).where(
                    IdPrincipal.tenant_id == tenant_id, IdPrincipal.username == "admin"
                )
            )
        ).scalar_one()
        regular = IdPrincipal(
            tenant_id=tenant_id,
            kind="human",
            username="regular",
            display_name="Regular",
            status="active",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add(regular)
        await db.flush()
        db.add(
            IdCredential(
                tenant_id=tenant_id,
                principal_id=regular.id,
                kind="password",
                secret_hash=hash_password("regular-pw"),
                status="active",
                created_by=admin.id,
                updated_by=admin.id,
            )
        )
        await db.commit()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            await c.post(
                "/auth/login",
                json={
                    "tenant_slug": ids["tenant_slug"],
                    "username": "regular",
                    "password": "regular-pw",
                },
            )
            forbidden = await c.post(
                "/md/charts-of-accounts",
                json={"code": "X", "name": "denied"},
            )
            assert forbidden.status_code == 403
            body = forbidden.json()
            assert body["domain"] == "md.chart_of_accounts"
            assert body["action"] == "create"
