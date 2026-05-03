"""Integration tests for the authorisation evaluator and SoD enforcement.

Exercises the full path: bootstrap a tenant + admin (gets SYSTEM_TENANT_ADMIN),
seed a second principal with a chosen role binding, then call evaluate /
enforce and assert the outcome + decision-log row.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from openspine.core.errors import AuthorisationError, SoDViolationError
from openspine.db import SessionFactory
from openspine.identity import service
from openspine.identity.authz import (
    enforce,
    evaluate,
    load_effective_permissions,
)
from openspine.identity.context import PrincipalContext
from openspine.identity.models import IdPrincipal
from openspine.identity.rbac_models import (
    IdAuthDecisionLog,
    IdPrincipalRole,
    IdRoleSingle,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def tenant_with_users() -> AsyncIterator[dict[str, str]]:
    """Bootstrap a tenant + admin, then create three additional users:
    - clerk: holds USER_CREATE only
    - releaser: holds FI_AP_PAYMENT_RELEASE only
    - sod_violator: holds both FI_AP_INVOICE_POST and FI_AP_PAYMENT_RELEASE
      (triggers SOD_AP_POST_AND_PAY block).
    """
    slug = f"authz-{uuid.uuid4().hex[:8]}"
    async with SessionFactory() as db:
        tenant, admin = await service.bootstrap_tenant_and_admin(
            db,
            tenant_name=f"Authz {slug}",
            tenant_slug=slug,
            admin_username="admin",
            admin_display_name="Authz Admin",
            admin_email=f"admin@{slug}.example",
            admin_password="authz-test-password",
        )
        await db.commit()

        # Create the three test principals + grant single roles.
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(t=str(tenant.id))
        )

        async def _make_principal(username: str) -> uuid.UUID:
            p = IdPrincipal(
                tenant_id=tenant.id,
                kind="human",
                username=username,
                display_name=username,
                status="active",
                created_by=admin.id,
                updated_by=admin.id,
            )
            db.add(p)
            await db.flush()
            return p.id

        clerk_id = await _make_principal("clerk")
        releaser_id = await _make_principal("releaser")
        violator_id = await _make_principal("violator")

        async def _grant_single(principal_id: uuid.UUID, role_code: str) -> None:
            role = (
                await db.execute(
                    select(IdRoleSingle).where(
                        IdRoleSingle.tenant_id == tenant.id,
                        IdRoleSingle.code == role_code,
                    )
                )
            ).scalar_one()
            db.add(
                IdPrincipalRole(
                    tenant_id=tenant.id,
                    principal_id=principal_id,
                    role_single_id=role.id,
                    created_by=admin.id,
                    updated_by=admin.id,
                )
            )
            await db.flush()

        await _grant_single(clerk_id, "USER_CREATE")
        await _grant_single(releaser_id, "FI_AP_PAYMENT_RELEASE")
        await _grant_single(violator_id, "FI_AP_INVOICE_POST")
        await _grant_single(violator_id, "FI_AP_PAYMENT_RELEASE")
        await db.commit()

        ids = {
            "tenant_id": str(tenant.id),
            "admin_id": str(admin.id),
            "clerk_id": str(clerk_id),
            "releaser_id": str(releaser_id),
            "violator_id": str(violator_id),
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


def _ctx(tenant_id: str, principal_id: str) -> PrincipalContext:
    return PrincipalContext(
        tenant_id=uuid.UUID(tenant_id),
        principal_id=uuid.UUID(principal_id),
        principal_kind="human",
        auth_method="session",
        trace_id=uuid.uuid4(),
    )


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


async def test_admin_holds_user_create_via_composite(
    tenant_with_users: dict[str, str],
) -> None:
    ctx = _ctx(tenant_with_users["tenant_id"], tenant_with_users["admin_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=tenant_with_users["tenant_id"]
            )
        )
        decision = await evaluate(db, ctx=ctx, domain="system.user", action="create")
        await db.commit()
    assert decision.outcome == "allow"


async def test_clerk_can_create_users_but_not_release_payments(
    tenant_with_users: dict[str, str],
) -> None:
    ctx = _ctx(tenant_with_users["tenant_id"], tenant_with_users["clerk_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=tenant_with_users["tenant_id"]
            )
        )
        ok = await evaluate(db, ctx=ctx, domain="system.user", action="create")
        denied = await evaluate(db, ctx=ctx, domain="fi.payment", action="release")
        await db.commit()
    assert ok.outcome == "allow"
    assert denied.outcome == "deny"
    assert denied.reason == "no_matching_permission"


async def test_anonymous_is_denied_without_a_db_lookup(
    tenant_with_users: dict[str, str],
) -> None:
    anon = PrincipalContext.anonymous(trace_id=uuid.uuid4())
    async with SessionFactory() as db:
        decision = await evaluate(db, ctx=anon, domain="system.user", action="create")
    assert decision.outcome == "deny"
    assert decision.reason == "not_authenticated"


async def test_unknown_auth_object_denies(
    tenant_with_users: dict[str, str],
) -> None:
    ctx = _ctx(tenant_with_users["tenant_id"], tenant_with_users["admin_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=tenant_with_users["tenant_id"]
            )
        )
        decision = await evaluate(db, ctx=ctx, domain="not.a.real.object", action="anything")
        await db.commit()
    assert decision.outcome == "deny"
    assert decision.reason == "unknown_auth_object"


# ---------------------------------------------------------------------------
# SoD
# ---------------------------------------------------------------------------


async def test_sod_block_overrides_otherwise_valid_action(
    tenant_with_users: dict[str, str],
) -> None:
    """The violator holds both AP-post and AP-release. Either action is
    SoD-blocked even though they hold the matching single role."""
    ctx = _ctx(tenant_with_users["tenant_id"], tenant_with_users["violator_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=tenant_with_users["tenant_id"]
            )
        )
        decision = await evaluate(db, ctx=ctx, domain="fi.invoice.ap", action="post")
        await db.commit()
    assert decision.outcome == "sod_block"
    assert decision.reason == "sod_violation"
    assert decision.sod_rule_id is not None


async def test_enforce_raises_sod_violation_for_blocked_principal(
    tenant_with_users: dict[str, str],
) -> None:
    ctx = _ctx(tenant_with_users["tenant_id"], tenant_with_users["violator_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=tenant_with_users["tenant_id"]
            )
        )
        with pytest.raises(SoDViolationError):
            await enforce(db, ctx=ctx, domain="fi.payment", action="release")


async def test_enforce_raises_authorisation_error_for_clerk_releasing_payment(
    tenant_with_users: dict[str, str],
) -> None:
    ctx = _ctx(tenant_with_users["tenant_id"], tenant_with_users["clerk_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=tenant_with_users["tenant_id"]
            )
        )
        with pytest.raises(AuthorisationError):
            await enforce(db, ctx=ctx, domain="fi.payment", action="release")


# ---------------------------------------------------------------------------
# Effective permissions
# ---------------------------------------------------------------------------


async def test_effective_permissions_for_admin_includes_token_issue(
    tenant_with_users: dict[str, str],
) -> None:
    tenant_id = uuid.UUID(tenant_with_users["tenant_id"])
    admin_id = uuid.UUID(tenant_with_users["admin_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=tenant_with_users["tenant_id"]
            )
        )
        perms = await load_effective_permissions(db, tenant_id=tenant_id, principal_id=admin_id)
    pairs = {(p.domain, p.action_code) for p in perms}
    assert ("system.token", "issue") in pairs
    assert ("system.user", "create") in pairs
    assert ("id.audit", "read_all") in pairs


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------


async def test_decision_log_row_written_on_each_evaluate(
    tenant_with_users: dict[str, str],
) -> None:
    ctx = _ctx(tenant_with_users["tenant_id"], tenant_with_users["clerk_id"])
    async with SessionFactory() as db:
        await db.execute(
            text("SELECT set_config('openspine.tenant_id', :t, true)").bindparams(
                t=tenant_with_users["tenant_id"]
            )
        )
        await evaluate(db, ctx=ctx, domain="system.user", action="create")
        await evaluate(db, ctx=ctx, domain="fi.payment", action="release")
        await db.commit()

        rows = (
            (
                await db.execute(
                    select(IdAuthDecisionLog)
                    .where(
                        IdAuthDecisionLog.tenant_id == ctx.tenant_id,
                        IdAuthDecisionLog.principal_id == ctx.principal_id,
                    )
                    .order_by(IdAuthDecisionLog.evaluated_at.asc())
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    decisions = [r.decision for r in rows]
    assert "allow" in decisions
    assert "deny" in decisions
