"""Authorisation evaluator + `@requires_auth` decorator.

Per `docs/identity/permissions.md`, every service method that mutates
data or exposes sensitive reads must check authority. The check is:

    (principal, domain, action, *qualifier_values) → allow | deny | sod_block

`evaluate(...)` runs the check. It loads the principal's effective
permissions (role bindings → composite expansion → single-role
permissions, then intersected with the binding's scope qualifiers),
evaluates qualifiers, runs the SoD check, writes a decision-log row,
and returns or raises.

`@requires_auth(domain, action, **qualifier_extractors)` is the
decorator FastAPI route handlers use. Qualifier extractors are
callables from `request.state` + arguments to the qualifier value;
they are evaluated lazily so simple checks don't pay for unused
extractors.

Decision-log writes go into the same DB transaction as the action
they cover. v0.1 writes synchronously; a future batched writer can
move them off the request path without changing the schema.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openspine.core.errors import AuthorisationError, SoDViolationError
from openspine.identity.context import PrincipalContext
from openspine.identity.middleware import get_request_session
from openspine.identity.rbac_models import (
    IdAuthDecisionLog,
    IdAuthObject,
    IdPermission,
    IdPrincipalRole,
    IdRoleCompositeMember,
    IdSodRule,
    IdSodRuleClause,
)


@dataclass(frozen=True)
class EffectivePermission:
    """One row in the principal's compiled permission set.

    `binding_scope` is the qualifier overlay from the role assignment;
    `permission_qualifiers` is the qualifier shape declared on the
    role itself. The evaluator intersects the two at check time.
    """

    domain: str
    action_code: str
    permission_qualifiers: dict[str, Any]
    binding_scope: dict[str, Any] = field(default_factory=dict)
    auth_object_id: uuid.UUID | None = None
    role_single_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Decision:
    """The evaluator's verdict + the audit context.

    `outcome` ∈ {'allow', 'deny', 'sod_block'}. `reason` is a short
    machine-readable code; `attempted` / `allowed` mirror the
    structured-error envelope in `docs/identity/permissions.md`.
    """

    outcome: str
    reason: str | None
    attempted: dict[str, Any]
    allowed: dict[str, Any] | None
    matched_role_single_id: uuid.UUID | None = None
    sod_rule_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Qualifier matching
# ---------------------------------------------------------------------------


def _match_string_list(allowed_value: Any, attempted: Any) -> bool:
    """`["A", "B"]` allows {"A","B"}; `["*"]` or missing allows anything."""
    if allowed_value is None:
        return True
    if isinstance(allowed_value, list):
        if "*" in allowed_value:
            return True
        return attempted in allowed_value
    return bool(attempted == allowed_value)


def _match_numeric_range(allowed_value: Any, attempted: Any) -> bool:
    """`{"min": x, "max": y}` allows x ≤ attempted ≤ y."""
    if allowed_value is None:
        return True
    if not isinstance(allowed_value, dict):
        return False
    try:
        att = Decimal(str(attempted))
    except (TypeError, ValueError):
        return False
    min_v = allowed_value.get("min")
    max_v = allowed_value.get("max")
    if min_v is not None and att < Decimal(str(min_v)):
        return False
    if max_v is not None and att > Decimal(str(max_v)):
        return False
    return True


def _match_amount_range(allowed_value: Any, attempted: Any) -> bool:
    """`{"max": 10000, "currency": "EUR"}` vs `{"amount": ..., "currency": ...}`.

    v0.1 simplification: only same-currency comparisons are supported. FX
    conversion at rate type 'M' lands when the FX-rate service is wired
    (post-§4.4). Cross-currency calls return False (deny by default —
    safer than a silently-wrong allow).
    """
    if allowed_value is None:
        return True
    if not isinstance(allowed_value, dict) or not isinstance(attempted, dict):
        return False
    if allowed_value.get("currency") != attempted.get("currency"):
        return False
    try:
        att = Decimal(str(attempted.get("amount")))
        cap = Decimal(str(allowed_value.get("max")))
    except (TypeError, ValueError):
        return False
    return att <= cap


def _match_wildcard(allowed_value: Any, attempted: Any) -> bool:
    """Like string_list, but a top-level `"*"` also allows anything."""
    if allowed_value == "*":
        return True
    return _match_string_list(allowed_value, attempted)


_MATCHERS: dict[str, Callable[[Any, Any], bool]] = {
    "string_list": _match_string_list,
    "numeric_range": _match_numeric_range,
    "amount_range": _match_amount_range,
    "wildcard": _match_wildcard,
}


def _qualifier_matches(
    permission_quals: dict[str, Any],
    binding_scope: dict[str, Any],
    attempted: dict[str, Any],
    qualifier_data_types: dict[str, str],
) -> tuple[bool, str | None]:
    """Return (allowed, failing_qualifier_or_None).

    For each attempted qualifier, both the permission's value AND the
    binding scope's value must allow it. A missing qualifier on either
    side is "no constraint" (allow). Unknown data types deny (defensive
    default — better to deny a misconfigured permission than to wave
    it through).
    """
    for qual_code, attempted_value in attempted.items():
        data_type = qualifier_data_types.get(qual_code)
        if data_type is None:
            # The auth object doesn't declare this qualifier. Either the
            # caller is asking with a stale shape or the qualifier was
            # added after the permission was granted. Permissive by
            # default — an undeclared qualifier doesn't deny.
            continue
        matcher = _MATCHERS.get(data_type)
        if matcher is None:
            return False, qual_code
        if not matcher(permission_quals.get(qual_code), attempted_value):
            return False, qual_code
        if not matcher(binding_scope.get(qual_code), attempted_value):
            return False, qual_code
    return True, None


# ---------------------------------------------------------------------------
# Effective-permission loading
# ---------------------------------------------------------------------------


async def load_effective_permissions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
) -> list[EffectivePermission]:
    """Load every (domain, action, qualifiers) the principal can invoke.

    Walks role bindings → composite expansion → single roles → permissions,
    joining `id_auth_object` to materialise the human-readable domain.

    Time-bounded bindings (`valid_from`/`valid_to`) are filtered to
    "currently valid".
    """
    now = datetime.now(UTC)
    bindings_stmt = select(IdPrincipalRole).where(
        IdPrincipalRole.tenant_id == tenant_id,
        IdPrincipalRole.principal_id == principal_id,
        or_(IdPrincipalRole.valid_from.is_(None), IdPrincipalRole.valid_from <= now),
        or_(IdPrincipalRole.valid_to.is_(None), IdPrincipalRole.valid_to > now),
    )
    bindings = (await session.execute(bindings_stmt)).scalars().all()

    # Resolve every binding to the set of single role ids it grants.
    single_role_ids_with_scope: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for binding in bindings:
        scope = binding.scope_qualifiers or {}
        if binding.role_single_id is not None:
            single_role_ids_with_scope.append((binding.role_single_id, scope))
        elif binding.role_composite_id is not None:
            members_stmt = select(IdRoleCompositeMember.single_id).where(
                IdRoleCompositeMember.composite_id == binding.role_composite_id,
            )
            for (sid,) in (await session.execute(members_stmt)).all():
                single_role_ids_with_scope.append((sid, scope))

    if not single_role_ids_with_scope:
        return []

    # One query for all permissions joined to their auth-object domains.
    role_ids = list({sid for sid, _ in single_role_ids_with_scope})
    perm_stmt = (
        select(
            IdPermission.role_single_id,
            IdPermission.auth_object_id,
            IdPermission.action_code,
            IdPermission.qualifier_values,
            IdAuthObject.domain,
        )
        .join(IdAuthObject, IdPermission.auth_object_id == IdAuthObject.id)
        .where(IdPermission.role_single_id.in_(role_ids))
    )
    perm_rows = (await session.execute(perm_stmt)).all()

    out: list[EffectivePermission] = []
    for sid, scope in single_role_ids_with_scope:
        for pr in perm_rows:
            if pr.role_single_id != sid:
                continue
            out.append(
                EffectivePermission(
                    domain=pr.domain,
                    action_code=pr.action_code,
                    permission_qualifiers=pr.qualifier_values or {},
                    binding_scope=scope,
                    auth_object_id=pr.auth_object_id,
                    role_single_id=sid,
                )
            )
    return out


# ---------------------------------------------------------------------------
# SoD evaluation
# ---------------------------------------------------------------------------


async def find_active_sod_violation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    permissions: list[EffectivePermission],
) -> tuple[uuid.UUID, str] | None:
    """Return (sod_rule_id, severity) of the first matching block-rule.

    A SoD rule is violated when EVERY clause `(domain, action)` is
    present in the principal's effective permission set. Block rules
    are checked first; warn rules are returned but treated as advisory
    by the caller (they audit but don't deny).
    """
    if not permissions:
        return None
    granted = {(p.domain, p.action_code) for p in permissions}

    rules = (
        (await session.execute(select(IdSodRule).where(IdSodRule.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    if not rules:
        return None

    rule_ids = [r.id for r in rules]
    clauses_rows = (
        await session.execute(
            select(
                IdSodRuleClause.sod_rule_id,
                IdSodRuleClause.action_code,
                IdAuthObject.domain,
            )
            .join(IdAuthObject, IdSodRuleClause.auth_object_id == IdAuthObject.id)
            .where(IdSodRuleClause.sod_rule_id.in_(rule_ids))
        )
    ).all()

    by_rule: dict[uuid.UUID, set[tuple[str, str]]] = {}
    for c in clauses_rows:
        by_rule.setdefault(c.sod_rule_id, set()).add((c.domain, c.action_code))

    # Block rules first — denial outranks warning.
    for r in sorted(rules, key=lambda r: 0 if r.severity == "block" else 1):
        clauses = by_rule.get(r.id, set())
        if clauses and clauses.issubset(granted):
            return r.id, r.severity
    return None


# ---------------------------------------------------------------------------
# Top-level evaluator
# ---------------------------------------------------------------------------


async def evaluate(
    session: AsyncSession,
    *,
    ctx: PrincipalContext,
    domain: str,
    action: str,
    qualifier_values: dict[str, Any] | None = None,
) -> Decision:
    """Evaluate a principal's authority for `(domain, action, qualifier_values)`.

    Order:
    1. Anonymous → deny immediately.
    2. Load effective permissions.
    3. Check SoD first — a block-rule violation overrides any allow.
    4. Find any matching permission whose qualifier shape allows the
       attempted values.
    5. Write a decision-log row; return Decision.

    Doesn't raise — see `enforce()` for the raising variant the
    decorator uses.
    """
    qualifier_values = qualifier_values or {}
    if ctx.is_anonymous or ctx.tenant_id is None or ctx.principal_id is None:
        decision = Decision(
            outcome="deny",
            reason="not_authenticated",
            attempted=qualifier_values,
            allowed=None,
        )
        return decision

    # Load qualifier data-type map for the auth object — needed for matching.
    ao_row = (
        await session.execute(
            select(IdAuthObject).where(
                IdAuthObject.tenant_id == ctx.tenant_id,
                IdAuthObject.domain == domain,
            )
        )
    ).scalar_one_or_none()
    if ao_row is None:
        decision = Decision(
            outcome="deny",
            reason="unknown_auth_object",
            attempted=qualifier_values,
            allowed=None,
        )
        await _write_decision_log(session, ctx=ctx, domain=domain, action=action, decision=decision)
        return decision

    qualifier_data_types = await _load_qualifier_types(session, auth_object_id=ao_row.id)

    permissions = await load_effective_permissions(
        session, tenant_id=ctx.tenant_id, principal_id=ctx.principal_id
    )

    # SoD before allow.
    sod = await find_active_sod_violation(session, tenant_id=ctx.tenant_id, permissions=permissions)
    if sod is not None:
        sod_rule_id, severity = sod
        if severity == "block":
            decision = Decision(
                outcome="sod_block",
                reason="sod_violation",
                attempted=qualifier_values,
                allowed=None,
                sod_rule_id=sod_rule_id,
            )
            await _write_decision_log(
                session, ctx=ctx, domain=domain, action=action, decision=decision
            )
            return decision
        # severity == 'warn' — fall through; the violation still gets
        # logged below alongside the allow/deny outcome.

    # Permission match.
    match: EffectivePermission | None = None
    failing_qualifier: str | None = None
    for perm in permissions:
        if perm.domain != domain or perm.action_code != action:
            continue
        ok, fail = _qualifier_matches(
            perm.permission_qualifiers,
            perm.binding_scope,
            qualifier_values,
            qualifier_data_types,
        )
        if ok:
            match = perm
            failing_qualifier = None
            break
        failing_qualifier = fail or failing_qualifier

    if match is not None:
        decision = Decision(
            outcome="allow",
            reason=None,
            attempted=qualifier_values,
            allowed=match.permission_qualifiers,
            matched_role_single_id=match.role_single_id,
            sod_rule_id=sod[0] if sod else None,
        )
    elif failing_qualifier is not None:
        decision = Decision(
            outcome="deny",
            reason=f"qualifier_failed:{failing_qualifier}",
            attempted=qualifier_values,
            allowed=None,
        )
    else:
        decision = Decision(
            outcome="deny",
            reason="no_matching_permission",
            attempted=qualifier_values,
            allowed=None,
        )

    await _write_decision_log(session, ctx=ctx, domain=domain, action=action, decision=decision)
    return decision


async def enforce(
    session: AsyncSession,
    *,
    ctx: PrincipalContext,
    domain: str,
    action: str,
    qualifier_values: dict[str, Any] | None = None,
) -> Decision:
    """Like `evaluate`, but raises on deny / sod_block.

    The `@requires_auth` decorator calls this. Plain service code can
    call `evaluate` directly when it wants to inspect the decision
    without raising.
    """
    decision = await evaluate(
        session,
        ctx=ctx,
        domain=domain,
        action=action,
        qualifier_values=qualifier_values,
    )
    if decision.outcome == "allow":
        return decision
    if decision.outcome == "sod_block":
        raise SoDViolationError(
            "operation forbidden by segregation of duties",
            domain=domain,
            action=action,
            reason=decision.reason,
            attempted=decision.attempted,
        )
    raise AuthorisationError(
        "operation not permitted",
        domain=domain,
        action=action,
        reason=decision.reason,
        attempted=decision.attempted,
        allowed=decision.allowed,
    )


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


# Type alias for qualifier extractors. Each extractor receives the
# request and the route's keyword arguments and returns the qualifier
# value. The decorator passes through positional + keyword args so
# extractors can read them.
QualifierExtractor = Callable[..., Any]


def requires_auth(
    domain: str, action: str, **extractors: QualifierExtractor
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: enforce `(domain, action)` authority on a route.

    Usage:

        @router.post("/ap-invoices")
        @requires_auth(
            "fi.invoice.ap",
            "post",
            company_code=lambda payload, **_: payload.company_code,
            amount_range=lambda payload, **_: {
                "amount": payload.amount, "currency": payload.currency
            },
        )
        async def post_invoice(payload: APInvoicePayload, request: Request): ...

    The first positional argument to extractors is the request body
    (whichever Pydantic model the route declares); request and other
    kwargs are passed through. Extractors that don't need them ignore
    via `**_`.
    """
    import functools

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request = kwargs.get("request") or _find_request(args)
            if request is None:
                raise RuntimeError(
                    "@requires_auth requires the route to take "
                    "`request: Request` so the decorator can read the "
                    "principal context. Add it to the route signature."
                )
            ctx: PrincipalContext = request.state.principal_context
            session: AsyncSession = get_request_session()

            attempted: dict[str, Any] = {}
            for qual_code, extractor in extractors.items():
                try:
                    attempted[qual_code] = extractor(*args, **kwargs)
                except Exception as exc:
                    # An extractor that raises is a bug in the route;
                    # fail closed rather than silently allow.
                    raise AuthorisationError(
                        "qualifier extraction failed",
                        domain=domain,
                        action=action,
                        reason=f"extractor_error:{qual_code}",
                        attempted={"error": str(exc)},
                    ) from exc

            await enforce(
                session,
                ctx=ctx,
                domain=domain,
                action=action,
                qualifier_values=attempted,
            )
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def _find_request(args: tuple[Any, ...]) -> Request | None:
    for a in args:
        if isinstance(a, Request):
            return a
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_qualifier_types(
    session: AsyncSession, *, auth_object_id: uuid.UUID
) -> dict[str, str]:
    from openspine.identity.rbac_models import IdAuthObjectQualifier

    rows = (
        await session.execute(
            select(
                IdAuthObjectQualifier.qualifier_code,
                IdAuthObjectQualifier.data_type,
            ).where(IdAuthObjectQualifier.auth_object_id == auth_object_id)
        )
    ).all()
    return {qc: dt for qc, dt in rows}


async def _write_decision_log(
    session: AsyncSession,
    *,
    ctx: PrincipalContext,
    domain: str,
    action: str,
    decision: Decision,
) -> None:
    if ctx.tenant_id is None:
        # Anonymous denials don't get a tenant-scoped log row. The
        # auth_audit_event in id_audit_event covers them.
        return
    row = IdAuthDecisionLog(
        tenant_id=ctx.tenant_id,
        principal_id=ctx.principal_id,
        trace_id=ctx.trace_id,
        domain=domain,
        action_code=action,
        decision=decision.outcome,
        reason=decision.reason,
        qualifier_values=decision.attempted,
        matched_role_single_id=decision.matched_role_single_id,
        sod_rule_id=decision.sod_rule_id,
    )
    session.add(row)
    await session.flush()


__all__ = [
    "Decision",
    "EffectivePermission",
    "QualifierExtractor",
    "enforce",
    "evaluate",
    "find_active_sod_violation",
    "load_effective_permissions",
    "requires_auth",
]
