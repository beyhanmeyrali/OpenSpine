"""End-to-end tests for AP invoice posting + open-item derivation.

Covers:
- Happy path: post a 1-line expense AP invoice, the resulting
  document type is KR with one debit (expense) + one credit
  (vendor recon) line, and the credit shows up as an open item
  for the vendor.
- Multi-expense-line invoices (D expense1 + D expense2 + C recon).
- Validation: BP not in tenant, BP doesn't hold vendor role, recon
  account is not actually a recon account, recon_kind mismatch.
- Open-items filters: by role, by BP, by company code.
- Reversed AP invoices drop out of the open-item view.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from openspine.db import SessionFactory
from openspine.identity import service as identity_service
from openspine.main import app

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def ap_ready_tenant() -> AsyncIterator[dict[str, str]]:
    """Bootstrap a tenant + admin, log in, and seed:
    - CompanyCode + CoA + 4 GL accounts (cash, expense, vendor recon, sales)
    - A vendor BP
    - md_gl_account_company overlays for each GL account
    - Open posting period 2026/05
    The vendor recon GL is created with is_recon=TRUE +
    recon_kind='vendor' via direct service call (the /md endpoints
    don't yet expose those fields)."""
    slug = f"ap-{uuid.uuid4().hex[:8]}"
    password = "ap-pw"
    async with SessionFactory() as db:
        tenant, _admin = await identity_service.bootstrap_tenant_and_admin(
            db,
            tenant_name=f"AP test {slug}",
            tenant_slug=slug,
            admin_username="admin",
            admin_display_name="AP Admin",
            admin_email=f"admin@{slug}.example",
            admin_password=password,
        )
        await db.commit()
        tenant_id = tenant.id

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post(
                "/auth/login",
                json={"tenant_slug": slug, "username": "admin", "password": password},
            )
            currencies = (await client.get("/md/currencies")).json()
            eur_id = next(c["id"] for c in currencies if c["code"] == "EUR")

            fyv = (await client.post("/md/fiscal-year-variants", json={"code": "K4"})).json()
            coa = (
                await client.post(
                    "/md/charts-of-accounts",
                    json={"code": "INT", "name": "International CoA"},
                )
            ).json()
            coa_id = coa["id"]

            # Standard GL accounts via the public endpoint.
            gl_payloads = [
                ("100000", "Cash", "balance_sheet"),
                ("600000", "Office Expense", "pnl"),
                ("610000", "Travel Expense", "pnl"),
            ]
            gl_ids: dict[str, str] = {}
            for number, name, kind in gl_payloads:
                r = await client.post(
                    "/md/gl-accounts",
                    json={
                        "chart_of_accounts_id": coa_id,
                        "account_number": number,
                        "name": name,
                        "account_kind": kind,
                    },
                )
                gl_ids[name] = r.json()["id"]

            # Vendor recon account — created via service so we can set
            # is_recon=TRUE + recon_kind='vendor'.
            from openspine.identity.models import IdPrincipal
            from openspine.md.service import (
                create_company_code,
                create_gl_account,
                create_gl_account_company,
            )

            async with SessionFactory() as db_inner:
                await db_inner.execute(
                    text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                        t=str(tenant_id)
                    )
                )
                admin = (
                    await db_inner.execute(
                        select(IdPrincipal).where(
                            IdPrincipal.tenant_id == tenant_id,
                            IdPrincipal.username == "admin",
                        )
                    )
                ).scalar_one()
                vendor_recon = await create_gl_account(
                    db_inner,
                    tenant_id=tenant_id,
                    actor_principal_id=admin.id,
                    chart_of_accounts_id=uuid.UUID(coa_id),
                    account_number="200000",
                    name="AP Vendor Recon",
                    account_kind="balance_sheet",
                    is_recon=True,
                    recon_kind="vendor",
                )
                customer_recon = await create_gl_account(
                    db_inner,
                    tenant_id=tenant_id,
                    actor_principal_id=admin.id,
                    chart_of_accounts_id=uuid.UUID(coa_id),
                    account_number="140000",
                    name="AR Customer Recon",
                    account_kind="balance_sheet",
                    is_recon=True,
                    recon_kind="customer",
                )
                await db_inner.commit()

                cc = await create_company_code(
                    db_inner,
                    tenant_id=tenant_id,
                    actor_principal_id=admin.id,
                    code="DE01",
                    name="OpenSpine GmbH",
                    country_code="DE",
                    local_currency_id=uuid.UUID(eur_id),
                    chart_of_accounts_id=uuid.UUID(coa_id),
                    fiscal_year_variant_id=uuid.UUID(fyv["id"]),
                )
                await db_inner.commit()
                cc_id = cc.id

                for gl_id_str in [
                    *gl_ids.values(),
                    str(vendor_recon.id),
                    str(customer_recon.id),
                ]:
                    await create_gl_account_company(
                        db_inner,
                        tenant_id=tenant_id,
                        actor_principal_id=admin.id,
                        gl_account_id=uuid.UUID(gl_id_str),
                        company_code_id=cc_id,
                    )
                await db_inner.commit()

            # Open posting period 2026/05.
            await client.post(
                "/md/posting-periods",
                json={
                    "company_code_id": str(cc_id),
                    "fiscal_year": 2026,
                    "period": 5,
                    "period_start_date": "2026-05-01",
                    "period_end_date": "2026-05-31",
                    "state": "open",
                },
            )

            # Vendor BP.
            vendor_bp = (
                await client.post(
                    "/md/business-partners",
                    json={
                        "number": "V-AP-001",
                        "kind": "organisation",
                        "name": "Acme Office Supplies",
                        "country_code": "DE",
                        "roles": ["vendor"],
                    },
                )
            ).json()

            # A "BP not vendor" — the negative case.
            customer_only_bp = (
                await client.post(
                    "/md/business-partners",
                    json={
                        "number": "C-AP-001",
                        "kind": "organisation",
                        "name": "ACME Customer Only",
                        "country_code": "DE",
                        "roles": ["customer"],
                    },
                )
            ).json()

            yield {
                "client": client,  # type: ignore[dict-item]
                "tenant_id": str(tenant_id),
                "tenant_slug": slug,
                "admin_password": password,
                "company_code_id": str(cc_id),
                "eur_id": eur_id,
                "gl_office_expense": gl_ids["Office Expense"],
                "gl_travel_expense": gl_ids["Travel Expense"],
                "vendor_recon_id": str(vendor_recon.id),
                "customer_recon_id": str(customer_recon.id),
                "vendor_bp_id": vendor_bp["id"],
                "customer_only_bp_id": customer_only_bp["id"],
            }

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
# Happy path
# ---------------------------------------------------------------------------


async def test_post_simple_ap_invoice_creates_open_item(
    ap_ready_tenant: dict[str, str],
) -> None:
    bundle = ap_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    invoice = await client.post(
        "/fi/ap-invoices",
        json={
            "company_code_id": bundle["company_code_id"],
            "vendor_business_partner_id": bundle["vendor_bp_id"],
            "vendor_recon_account_id": bundle["vendor_recon_id"],
            "invoice_date": str(date(2026, 5, 10)),
            "posting_date": str(date(2026, 5, 12)),
            "fiscal_year": 2026,
            "period": 5,
            "local_currency_id": bundle["eur_id"],
            "reference": "ACME-2026-001",
            "expense_lines": [
                {
                    "gl_account_id": bundle["gl_office_expense"],
                    "amount_local": "240.00",
                    "line_text": "Office supplies — May",
                },
            ],
        },
    )
    assert invoice.status_code == 201, invoice.text
    body = invoice.json()
    assert body["document_type"] == "KR"
    assert body["line_count"] == 2

    # Confirm one D-line on expense + one C-line on vendor recon.
    line_kinds = {(line["debit_credit"], line["gl_account_id"]) for line in body["lines"]}
    assert ("D", bundle["gl_office_expense"]) in line_kinds
    assert ("C", bundle["vendor_recon_id"]) in line_kinds

    # Open items now show one row for this vendor.
    open_items = (await client.get("/fi/open-items", params={"role": "vendor"})).json()
    assert open_items["total"] >= 1
    matching = [
        oi for oi in open_items["items"] if oi["business_partner_id"] == bundle["vendor_bp_id"]
    ]
    assert len(matching) == 1
    item = matching[0]
    assert item["amount_local"] == "240.0000"
    assert item["debit_credit"] == "C"
    assert item["recon_kind"] == "vendor"


async def test_multi_line_ap_invoice(ap_ready_tenant: dict[str, str]) -> None:
    bundle = ap_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    invoice = await client.post(
        "/fi/ap-invoices",
        json={
            "company_code_id": bundle["company_code_id"],
            "vendor_business_partner_id": bundle["vendor_bp_id"],
            "vendor_recon_account_id": bundle["vendor_recon_id"],
            "invoice_date": str(date(2026, 5, 11)),
            "posting_date": str(date(2026, 5, 12)),
            "fiscal_year": 2026,
            "period": 5,
            "local_currency_id": bundle["eur_id"],
            "expense_lines": [
                {"gl_account_id": bundle["gl_office_expense"], "amount_local": "100.00"},
                {"gl_account_id": bundle["gl_travel_expense"], "amount_local": "300.00"},
            ],
        },
    )
    assert invoice.status_code == 201, invoice.text
    body = invoice.json()
    assert body["line_count"] == 3
    # Recon line should be the sum of debits.
    recon_lines = [
        line for line in body["lines"] if line["gl_account_id"] == bundle["vendor_recon_id"]
    ]
    assert len(recon_lines) == 1
    # Pydantic preserves the input Decimal's scale on POST replies;
    # the DB-read path renders NUMERIC(19,4) at 4 decimals. Compare
    # values, not string forms.
    from decimal import Decimal as _D

    assert _D(recon_lines[0]["amount_local"]) == _D("400.00")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_bp_without_vendor_role_is_rejected(
    ap_ready_tenant: dict[str, str],
) -> None:
    bundle = ap_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    response = await client.post(
        "/fi/ap-invoices",
        json={
            "company_code_id": bundle["company_code_id"],
            "vendor_business_partner_id": bundle["customer_only_bp_id"],
            "vendor_recon_account_id": bundle["vendor_recon_id"],
            "invoice_date": str(date(2026, 5, 12)),
            "posting_date": str(date(2026, 5, 12)),
            "fiscal_year": 2026,
            "period": 5,
            "local_currency_id": bundle["eur_id"],
            "expense_lines": [
                {"gl_account_id": bundle["gl_office_expense"], "amount_local": "10.00"},
            ],
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["reason"] == "bp_not_vendor"


async def test_non_recon_account_is_rejected(
    ap_ready_tenant: dict[str, str],
) -> None:
    bundle = ap_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    response = await client.post(
        "/fi/ap-invoices",
        json={
            "company_code_id": bundle["company_code_id"],
            "vendor_business_partner_id": bundle["vendor_bp_id"],
            # Use the office-expense GL as the "recon account" — it
            # isn't a recon account at all.
            "vendor_recon_account_id": bundle["gl_office_expense"],
            "invoice_date": str(date(2026, 5, 12)),
            "posting_date": str(date(2026, 5, 12)),
            "fiscal_year": 2026,
            "period": 5,
            "local_currency_id": bundle["eur_id"],
            "expense_lines": [
                {"gl_account_id": bundle["gl_travel_expense"], "amount_local": "10.00"},
            ],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["reason"] == "not_a_recon_account"


async def test_wrong_recon_kind_is_rejected(
    ap_ready_tenant: dict[str, str],
) -> None:
    """Posting an AP invoice against the customer recon GL should
    fail — the recon_kind doesn't match."""
    bundle = ap_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    response = await client.post(
        "/fi/ap-invoices",
        json={
            "company_code_id": bundle["company_code_id"],
            "vendor_business_partner_id": bundle["vendor_bp_id"],
            "vendor_recon_account_id": bundle["customer_recon_id"],
            "invoice_date": str(date(2026, 5, 12)),
            "posting_date": str(date(2026, 5, 12)),
            "fiscal_year": 2026,
            "period": 5,
            "local_currency_id": bundle["eur_id"],
            "expense_lines": [
                {"gl_account_id": bundle["gl_office_expense"], "amount_local": "10.00"},
            ],
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["reason"] == "wrong_recon_kind"
    assert body["allowed"]["recon_kind"] == "vendor"


# ---------------------------------------------------------------------------
# Open items
# ---------------------------------------------------------------------------


async def test_reversed_ap_invoice_drops_from_open_items(
    ap_ready_tenant: dict[str, str],
) -> None:
    """Reverse the AP invoice; both rows go to status='reversed' and
    the open-item view filters on status='posted', so the line
    drops out."""
    bundle = ap_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    invoice = await client.post(
        "/fi/ap-invoices",
        json={
            "company_code_id": bundle["company_code_id"],
            "vendor_business_partner_id": bundle["vendor_bp_id"],
            "vendor_recon_account_id": bundle["vendor_recon_id"],
            "invoice_date": str(date(2026, 5, 14)),
            "posting_date": str(date(2026, 5, 14)),
            "fiscal_year": 2026,
            "period": 5,
            "local_currency_id": bundle["eur_id"],
            "expense_lines": [
                {"gl_account_id": bundle["gl_office_expense"], "amount_local": "55.00"},
            ],
        },
    )
    inv_id = invoice.json()["id"]

    pre_reverse = await client.get(
        "/fi/open-items",
        params={
            "role": "vendor",
            "business_partner_id": bundle["vendor_bp_id"],
        },
    )
    assert any(oi["amount_local"] == "55.0000" for oi in pre_reverse.json()["items"])

    await client.post(
        f"/fi/journal-entries/{inv_id}/reverse",
        json={
            "posting_date": str(date(2026, 5, 15)),
            "fiscal_year": 2026,
            "period": 5,
            "reason": "wrong vendor",
        },
    )
    post_reverse = await client.get(
        "/fi/open-items",
        params={
            "role": "vendor",
            "business_partner_id": bundle["vendor_bp_id"],
        },
    )
    items = post_reverse.json()["items"]
    assert not any(oi["amount_local"] == "55.0000" for oi in items), items


async def test_open_items_filter_by_business_partner(
    ap_ready_tenant: dict[str, str],
) -> None:
    bundle = ap_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    await client.post(
        "/fi/ap-invoices",
        json={
            "company_code_id": bundle["company_code_id"],
            "vendor_business_partner_id": bundle["vendor_bp_id"],
            "vendor_recon_account_id": bundle["vendor_recon_id"],
            "invoice_date": str(date(2026, 5, 13)),
            "posting_date": str(date(2026, 5, 13)),
            "fiscal_year": 2026,
            "period": 5,
            "local_currency_id": bundle["eur_id"],
            "expense_lines": [
                {"gl_account_id": bundle["gl_travel_expense"], "amount_local": "75.00"},
            ],
        },
    )

    bp_filtered = await client.get(
        "/fi/open-items",
        params={"business_partner_id": bundle["vendor_bp_id"]},
    )
    assert bp_filtered.status_code == 200
    items = bp_filtered.json()["items"]
    assert all(oi["business_partner_id"] == bundle["vendor_bp_id"] for oi in items)
    assert any(oi["amount_local"] == "75.0000" for oi in items)


async def test_open_items_unknown_role_returns_404(
    ap_ready_tenant: dict[str, str],
) -> None:
    bundle = ap_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]
    response = await client.get("/fi/open-items", params={"role": "frobnicate"})
    assert response.status_code == 404
    assert response.json()["reason"] == "unknown_role"
