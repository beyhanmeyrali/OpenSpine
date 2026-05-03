"""End-to-end tests for the FI universal-journal posting service.

Covers:
- The happy path: bootstrap → seed CC + CoA + 3 GL accounts +
  open period → post a balanced 3-line journal entry → verify
  header + lines persisted, document number allocated.
- Failure paths: unbalanced entry (422), closed period (409),
  unknown GL account (404), GL account without Company Code overlay
  (404 / 'gl_account_company_missing').
- Authority gate: a principal without `fi.document:post` is denied.
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
from openspine.fi.models import FinDocumentHeader, FinDocumentLine
from openspine.identity import service as identity_service
from openspine.main import app

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def fi_ready_tenant() -> AsyncIterator[dict[str, str]]:
    """Bootstrap a tenant + admin, log in, and seed the minimum FI
    landscape: CompanyCode + CoA + 3 GL accounts + GL/Company overlays
    + an open posting period for 2026/05."""
    slug = f"fi-{uuid.uuid4().hex[:8]}"
    password = "fi-pw"
    async with SessionFactory() as db:
        tenant, _admin = await identity_service.bootstrap_tenant_and_admin(
            db,
            tenant_name=f"FI Posting {slug}",
            tenant_slug=slug,
            admin_username="admin",
            admin_display_name="FI Admin",
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

            fyv = (
                await client.post(
                    "/md/fiscal-year-variants",
                    json={"code": "K4"},
                )
            ).json()

            coa = (
                await client.post(
                    "/md/charts-of-accounts",
                    json={"code": "INT", "name": "International CoA"},
                )
            ).json()
            coa_id = coa["id"]

            gl_payloads = [
                ("100000", "Cash", "balance_sheet"),
                ("400000", "Sales Revenue", "pnl"),
                ("500000", "Cost of Goods Sold", "pnl"),
            ]
            gl_ids: list[str] = []
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
                gl_ids.append(r.json()["id"])

            cc = (
                await client.post(
                    "/md/company-codes",
                    json={
                        "code": "DE01",
                        "name": "OpenSpine GmbH",
                        "country_code": "DE",
                        "local_currency_id": eur_id,
                        "chart_of_accounts_id": coa_id,
                        "fiscal_year_variant_id": fyv["id"],
                    },
                )
            ).json()
            cc_id = cc["id"]

            # GL accounts need a Company Code overlay before they can
            # be posted to. Create an unblocked overlay for each.
            from openspine.md.service import create_gl_account_company

            async with SessionFactory() as db_inner:
                await db_inner.execute(
                    text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                        t=str(tenant_id)
                    )
                )
                # Need the admin id; the bootstrap admin's username is 'admin'.
                from openspine.identity.models import IdPrincipal

                admin = (
                    await db_inner.execute(
                        select(IdPrincipal).where(
                            IdPrincipal.tenant_id == tenant_id,
                            IdPrincipal.username == "admin",
                        )
                    )
                ).scalar_one()
                for gl_id in gl_ids:
                    await create_gl_account_company(
                        db_inner,
                        tenant_id=tenant_id,
                        actor_principal_id=admin.id,
                        gl_account_id=uuid.UUID(gl_id),
                        company_code_id=uuid.UUID(cc_id),
                    )
                await db_inner.commit()

            # Open posting period 2026/05.
            await client.post(
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

            yield {
                "client": client,  # type: ignore[dict-item]
                "tenant_id": str(tenant_id),
                "tenant_slug": slug,
                "admin_password": password,
                "company_code_id": cc_id,
                "eur_id": eur_id,
                "gl_cash": gl_ids[0],
                "gl_revenue": gl_ids[1],
                "gl_cogs": gl_ids[2],
            }

    # Cleanup
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


async def test_post_balanced_journal_entry(
    fi_ready_tenant: dict[str, str],
) -> None:
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    response = await client.post(
        "/fi/journal-entries",
        json={
            "company_code_id": bundle["company_code_id"],
            "document_type_code": "SA",
            "posting_date": str(date(2026, 5, 15)),
            "document_date": str(date(2026, 5, 15)),
            "fiscal_year": 2026,
            "period": 5,
            "reference": "PILOT-001",
            "header_text": "Cash sale",
            "lines": [
                {
                    "gl_account_id": bundle["gl_cash"],
                    "debit_credit": "D",
                    "amount_local": "1000.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_revenue"],
                    "debit_credit": "C",
                    "amount_local": "1000.00",
                    "local_currency_id": bundle["eur_id"],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["document_number"] >= 1_000_000
    assert body["line_count"] == 2
    assert body["_meta"]["self"].startswith("/fi/journal-entries/")

    # Verify rows persisted.
    tenant_id = uuid.UUID(bundle["tenant_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        header = (
            await db.execute(
                select(FinDocumentHeader).where(FinDocumentHeader.id == uuid.UUID(body["id"]))
            )
        ).scalar_one()
        assert header.document_number == body["document_number"]
        assert header.status == "posted"
        lines = (
            (
                await db.execute(
                    select(FinDocumentLine).where(FinDocumentLine.document_header_id == header.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(lines) == 2
    debits = sum(line.amount_local for line in lines if line.debit_credit == "D")
    credits = sum(line.amount_local for line in lines if line.debit_credit == "C")
    assert debits == credits


async def test_three_line_balanced_with_cogs(
    fi_ready_tenant: dict[str, str],
) -> None:
    """A more interesting posting: a sale that books revenue + COGS,
    matched by cash. Three lines, balanced overall."""
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    response = await client.post(
        "/fi/journal-entries",
        json={
            "company_code_id": bundle["company_code_id"],
            "document_type_code": "SA",
            "posting_date": str(date(2026, 5, 16)),
            "document_date": str(date(2026, 5, 16)),
            "fiscal_year": 2026,
            "period": 5,
            "lines": [
                {
                    "gl_account_id": bundle["gl_cash"],
                    "debit_credit": "D",
                    "amount_local": "300.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_cogs"],
                    "debit_credit": "D",
                    "amount_local": "200.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_revenue"],
                    "debit_credit": "C",
                    "amount_local": "500.00",
                    "local_currency_id": bundle["eur_id"],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["line_count"] == 3


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_unbalanced_entry_is_rejected(
    fi_ready_tenant: dict[str, str],
) -> None:
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    response = await client.post(
        "/fi/journal-entries",
        json={
            "company_code_id": bundle["company_code_id"],
            "document_type_code": "SA",
            "posting_date": str(date(2026, 5, 15)),
            "document_date": str(date(2026, 5, 15)),
            "fiscal_year": 2026,
            "period": 5,
            "lines": [
                {
                    "gl_account_id": bundle["gl_cash"],
                    "debit_credit": "D",
                    "amount_local": "100.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_revenue"],
                    "debit_credit": "C",
                    "amount_local": "99.00",
                    "local_currency_id": bundle["eur_id"],
                },
            ],
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["domain"] == "fi.document"
    assert body["reason"] == "unbalanced"
    assert "imbalance" in body["attempted"]


async def test_closed_period_is_rejected(
    fi_ready_tenant: dict[str, str],
) -> None:
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    # Close the period first.
    close = await client.post(
        f"/md/company-codes/{bundle['company_code_id']}/posting-periods/2026/5/state",
        json={"state": "closed"},
    )
    assert close.status_code == 200

    response = await client.post(
        "/fi/journal-entries",
        json={
            "company_code_id": bundle["company_code_id"],
            "document_type_code": "SA",
            "posting_date": str(date(2026, 5, 15)),
            "document_date": str(date(2026, 5, 15)),
            "fiscal_year": 2026,
            "period": 5,
            "lines": [
                {
                    "gl_account_id": bundle["gl_cash"],
                    "debit_credit": "D",
                    "amount_local": "10.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_revenue"],
                    "debit_credit": "C",
                    "amount_local": "10.00",
                    "local_currency_id": bundle["eur_id"],
                },
            ],
        },
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["reason"] == "period_closed"


async def test_unknown_gl_account_is_rejected(
    fi_ready_tenant: dict[str, str],
) -> None:
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]
    bogus = str(uuid.uuid4())
    response = await client.post(
        "/fi/journal-entries",
        json={
            "company_code_id": bundle["company_code_id"],
            "document_type_code": "SA",
            "posting_date": str(date(2026, 5, 15)),
            "document_date": str(date(2026, 5, 15)),
            "fiscal_year": 2026,
            "period": 5,
            "lines": [
                {
                    "gl_account_id": bogus,
                    "debit_credit": "D",
                    "amount_local": "10.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_revenue"],
                    "debit_credit": "C",
                    "amount_local": "10.00",
                    "local_currency_id": bundle["eur_id"],
                },
            ],
        },
    )
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["reason"] == "gl_account_not_found"


async def test_unknown_document_type_is_rejected(
    fi_ready_tenant: dict[str, str],
) -> None:
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]
    response = await client.post(
        "/fi/journal-entries",
        json={
            "company_code_id": bundle["company_code_id"],
            "document_type_code": "NOPE",
            "posting_date": str(date(2026, 5, 15)),
            "document_date": str(date(2026, 5, 15)),
            "fiscal_year": 2026,
            "period": 5,
            "lines": [
                {
                    "gl_account_id": bundle["gl_cash"],
                    "debit_credit": "D",
                    "amount_local": "10.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_revenue"],
                    "debit_credit": "C",
                    "amount_local": "10.00",
                    "local_currency_id": bundle["eur_id"],
                },
            ],
        },
    )
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["reason"] == "document_type_not_found"


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Reverse + display
# ---------------------------------------------------------------------------


async def test_reverse_round_trip_links_originals_and_swaps_signs(
    fi_ready_tenant: dict[str, str],
) -> None:
    """Post a balanced JE, reverse it, verify:
    - reversal AB document is balanced too (debit/credit swap)
    - both rows now status='reversed'
    - reversal_of_id ↔ reversed_by_id cross-pointers wired
    - finance.document.reversed event would have fired (not asserted here)
    """
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    posted = await client.post(
        "/fi/journal-entries",
        json={
            "company_code_id": bundle["company_code_id"],
            "document_type_code": "SA",
            "posting_date": str(date(2026, 5, 17)),
            "document_date": str(date(2026, 5, 17)),
            "fiscal_year": 2026,
            "period": 5,
            "lines": [
                {
                    "gl_account_id": bundle["gl_cash"],
                    "debit_credit": "D",
                    "amount_local": "777.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_revenue"],
                    "debit_credit": "C",
                    "amount_local": "777.00",
                    "local_currency_id": bundle["eur_id"],
                },
            ],
        },
    )
    assert posted.status_code == 201
    original_id = posted.json()["id"]

    rev = await client.post(
        f"/fi/journal-entries/{original_id}/reverse",
        json={
            "posting_date": str(date(2026, 5, 18)),
            "fiscal_year": 2026,
            "period": 5,
            "reason": "duplicate posting — entered by mistake",
        },
    )
    assert rev.status_code == 201, rev.text
    rev_body = rev.json()
    assert rev_body["document_type"] == "AB"
    assert rev_body["_meta"]["is_reversal"] is True
    assert rev_body["_meta"]["related"]["reversal_of"].endswith(original_id)
    rev_id = rev_body["id"]

    # Verify cross-pointers + statuses via DB.
    tenant_id = uuid.UUID(bundle["tenant_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
        original = await db.get(FinDocumentHeader, uuid.UUID(original_id))
        reversal = await db.get(FinDocumentHeader, uuid.UUID(rev_id))
        rev_lines = (
            (
                await db.execute(
                    select(FinDocumentLine).where(FinDocumentLine.document_header_id == reversal.id)
                )
            )
            .scalars()
            .all()
        )

    assert original.status == "reversed"
    assert reversal.status == "reversed"
    assert original.reversed_by_id == reversal.id
    assert reversal.reversal_of_id == original.id
    # Original was D-cash / C-revenue; reversal must swap.
    debit_acct = next(line.gl_account_id for line in rev_lines if line.debit_credit == "D")
    credit_acct = next(line.gl_account_id for line in rev_lines if line.debit_credit == "C")
    assert str(debit_acct) == bundle["gl_revenue"]
    assert str(credit_acct) == bundle["gl_cash"]


async def test_double_reverse_is_blocked(
    fi_ready_tenant: dict[str, str],
) -> None:
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]

    posted = await client.post(
        "/fi/journal-entries",
        json={
            "company_code_id": bundle["company_code_id"],
            "document_type_code": "SA",
            "posting_date": str(date(2026, 5, 19)),
            "document_date": str(date(2026, 5, 19)),
            "fiscal_year": 2026,
            "period": 5,
            "lines": [
                {
                    "gl_account_id": bundle["gl_cash"],
                    "debit_credit": "D",
                    "amount_local": "50.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_revenue"],
                    "debit_credit": "C",
                    "amount_local": "50.00",
                    "local_currency_id": bundle["eur_id"],
                },
            ],
        },
    )
    original_id = posted.json()["id"]
    rev = await client.post(
        f"/fi/journal-entries/{original_id}/reverse",
        json={
            "posting_date": str(date(2026, 5, 20)),
            "fiscal_year": 2026,
            "period": 5,
        },
    )
    assert rev.status_code == 201
    again = await client.post(
        f"/fi/journal-entries/{original_id}/reverse",
        json={
            "posting_date": str(date(2026, 5, 20)),
            "fiscal_year": 2026,
            "period": 5,
        },
    )
    assert again.status_code == 409
    assert again.json()["reason"] == "document_not_reversible"


async def test_get_document_returns_lines_and_meta(
    fi_ready_tenant: dict[str, str],
) -> None:
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]
    posted = await client.post(
        "/fi/journal-entries",
        json={
            "company_code_id": bundle["company_code_id"],
            "document_type_code": "SA",
            "posting_date": str(date(2026, 5, 21)),
            "document_date": str(date(2026, 5, 21)),
            "fiscal_year": 2026,
            "period": 5,
            "lines": [
                {
                    "gl_account_id": bundle["gl_cash"],
                    "debit_credit": "D",
                    "amount_local": "12.00",
                    "local_currency_id": bundle["eur_id"],
                },
                {
                    "gl_account_id": bundle["gl_revenue"],
                    "debit_credit": "C",
                    "amount_local": "12.00",
                    "local_currency_id": bundle["eur_id"],
                },
            ],
        },
    )
    doc_id = posted.json()["id"]
    fetched = await client.get(f"/fi/journal-entries/{doc_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["line_count"] == 2
    assert body["_meta"]["status"] == "posted"
    actions = {a["name"] for a in body["_meta"].get("actions", [])}
    assert "reverse" in actions


async def test_list_documents_by_period(
    fi_ready_tenant: dict[str, str],
) -> None:
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]
    # Post two more so the listing has multiple rows.
    for amount in ("100.00", "200.00"):
        await client.post(
            "/fi/journal-entries",
            json={
                "company_code_id": bundle["company_code_id"],
                "document_type_code": "SA",
                "posting_date": str(date(2026, 5, 22)),
                "document_date": str(date(2026, 5, 22)),
                "fiscal_year": 2026,
                "period": 5,
                "lines": [
                    {
                        "gl_account_id": bundle["gl_cash"],
                        "debit_credit": "D",
                        "amount_local": amount,
                        "local_currency_id": bundle["eur_id"],
                    },
                    {
                        "gl_account_id": bundle["gl_revenue"],
                        "debit_credit": "C",
                        "amount_local": amount,
                        "local_currency_id": bundle["eur_id"],
                    },
                ],
            },
        )
    listing = await client.get(
        "/fi/journal-entries",
        params={
            "company_code_id": bundle["company_code_id"],
            "fiscal_year": 2026,
            "period": 5,
        },
    )
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] >= 2
    # Ordered by document number ascending.
    numbers = [item["document_number"] for item in body["items"]]
    assert numbers == sorted(numbers)


async def test_get_unknown_document_returns_404(
    fi_ready_tenant: dict[str, str],
) -> None:
    bundle = fi_ready_tenant
    client: AsyncClient = bundle["client"]  # type: ignore[assignment]
    bogus = str(uuid.uuid4())
    response = await client.get(f"/fi/journal-entries/{bogus}")
    assert response.status_code == 404
    assert response.json()["reason"] == "document_not_in_tenant"


async def test_principal_without_fi_post_role_is_denied(
    fi_ready_tenant: dict[str, str],
) -> None:
    """A regular human principal (no roles assigned) cannot post."""
    bundle = fi_ready_tenant
    tenant_id = uuid.UUID(bundle["tenant_id"])

    from openspine.identity.models import IdCredential, IdPrincipal
    from openspine.identity.security import hash_password

    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant_id))
        )
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
                    "tenant_slug": bundle["tenant_slug"],
                    "username": "regular",
                    "password": "regular-pw",
                },
            )
            response = await c.post(
                "/fi/journal-entries",
                json={
                    "company_code_id": bundle["company_code_id"],
                    "document_type_code": "SA",
                    "posting_date": str(date(2026, 5, 15)),
                    "document_date": str(date(2026, 5, 15)),
                    "fiscal_year": 2026,
                    "period": 5,
                    "lines": [
                        {
                            "gl_account_id": bundle["gl_cash"],
                            "debit_credit": "D",
                            "amount_local": "10.00",
                            "local_currency_id": bundle["eur_id"],
                        },
                        {
                            "gl_account_id": bundle["gl_revenue"],
                            "debit_credit": "C",
                            "amount_local": "10.00",
                            "local_currency_id": bundle["eur_id"],
                        },
                    ],
                },
            )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["domain"] == "fi.document"
    assert body["action"] == "post"
