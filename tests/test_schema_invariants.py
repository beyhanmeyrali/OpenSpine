"""Schema-level invariants from `docs/architecture/data-model.md`.

These run against the SQLAlchemy metadata, so no live database is
required. They catch the kinds of drift that are expensive to fix once
shipped:

- a new business table missing `tenant_id`
- a new business table missing the audit columns
- a new business table without an RLS policy declared in the migration
- a foreign key without a covering index
- any `VARCHAR(N)` column type (per data-model.md, all strings are TEXT)

The intent is to fail fast in PR if a future module forgets a convention.

`id_tenant` is the global registry by design (no `tenant_id`, no RLS) —
it is exempted explicitly. `id_audit_event` is append-only (no `version`,
no `updated_at`/`updated_by`) — also exempted explicitly. Other globals
(`md_uom`, `md_currency`, etc.) will be added to `_GLOBAL_CATALOGUES`
as they land in §4.4.
"""

from __future__ import annotations

import openspine.identity  # noqa: F401  (registers identity tables on metadata)
from openspine.core.database import metadata
from openspine.identity.models import TABLES_WITH_RLS, TABLES_WITH_UPDATE_TRIGGER
from openspine.identity.rbac_models import (
    RBAC_TABLES_WITH_RLS,
    RBAC_TABLES_WITH_UPDATE_TRIGGER,
)

_ALL_TABLES_WITH_UPDATE_TRIGGER = (
    *TABLES_WITH_UPDATE_TRIGGER,
    *RBAC_TABLES_WITH_UPDATE_TRIGGER,
)
_ALL_TABLES_WITH_RLS = (*TABLES_WITH_RLS, *RBAC_TABLES_WITH_RLS)

# Tables that legitimately do not carry `tenant_id` (and therefore have
# no tenant-isolation RLS policy). Listed by table name.
_GLOBAL_CATALOGUES: frozenset[str] = frozenset(
    {
        "id_tenant",  # global tenant registry
    }
)

# Tables that are append-only (no `updated_at`/`updated_by`/`version`,
# no UPDATE trigger). Listed separately because they still carry
# `tenant_id` (when scoped) and still get an RLS policy.
_APPEND_ONLY: frozenset[str] = frozenset(
    {
        "id_audit_event",
        # id_auth_decision_log uses `evaluated_at` instead of `created_at`
        # (the decision time, which can predate the row insert when the
        # writer batches). It carries `principal_id` (the actor) but no
        # `created_by` — the row isn't a principal-driven create, it's
        # a system audit. The tests below add it to _NO_AUDIT_COLUMNS as
        # the explicit exemption.
        "id_auth_decision_log",
    }
)

# Append-only tables where even the standard `created_at` / `created_by`
# columns aren't carried, because the table substitutes its own
# domain-specific columns (e.g., `evaluated_at` on the decision log).
_NO_AUDIT_COLUMNS: frozenset[str] = frozenset({"id_auth_decision_log"})

# Allowed table prefixes per data-model.md. `alembic_version` is the
# Alembic bookkeeping table, exempted.
_ALLOWED_PREFIXES: tuple[str, ...] = (
    "id_",
    "md_",
    "fin_",
    "co_",
    "mm_",
    "pp_",
    "ext_",  # plugin-owned tables (rare; usually columns, not tables)
    "alembic_",
)


def _all_business_tables() -> list[str]:
    """Tables registered on the shared metadata, excluding test fixtures."""
    return [
        name
        for name in metadata.tables
        # Test fixtures may register transient `fake_*` tables; skip them.
        if not name.startswith("fake_")
    ]


def test_every_business_table_has_tenant_id_or_is_global_catalogue() -> None:
    for name in _all_business_tables():
        if name in _GLOBAL_CATALOGUES:
            continue
        cols = {c.name for c in metadata.tables[name].columns}
        assert "tenant_id" in cols, (
            f"Table {name!r} is missing `tenant_id`. Add it via "
            f"BusinessTableMixin, or add {name!r} to _GLOBAL_CATALOGUES "
            "in this test if it is intentionally global."
        )


def test_every_business_table_has_audit_columns() -> None:
    required = {"created_at", "created_by"}
    update_cols = {"updated_at", "updated_by", "version"}
    for name in _all_business_tables():
        cols = {c.name for c in metadata.tables[name].columns}
        if name not in _NO_AUDIT_COLUMNS:
            assert required <= cols, f"{name!r} missing insert-audit columns: {required - cols}"
        if name in _APPEND_ONLY:
            assert not (update_cols & cols), (
                f"{name!r} is declared append-only but carries update columns: {update_cols & cols}"
            )
        else:
            assert update_cols <= cols, (
                f"{name!r} missing update-audit columns: {update_cols - cols}. "
                f"Mark it append-only via _APPEND_ONLY if intentional."
            )


_AUDIT_AUTHOR_FK_COLUMNS: frozenset[str] = frozenset(
    {
        "created_by",
        "updated_by",
        # `*_by_principal_id` are one-shot action authors (who revoked
        # this token, who closed this period, etc.). Same exemption
        # rationale as created_by/updated_by.
        "revoked_by_principal_id",
    }
)


def test_every_navigation_foreign_key_has_an_index() -> None:
    """Every navigation FK is either indexed by a dedicated index, by
    being the leading column of a multi-column index, or by being the
    leading column of a unique constraint (which Postgres backs with an
    index).

    `created_by` / `updated_by` are exempt by data-model.md: they are
    audit-author metadata, write-amplifying on every business write, and
    not used in routine reads. The index is added when admin query
    patterns make it necessary, not prophylactically.
    """
    from sqlalchemy import UniqueConstraint

    for name in _all_business_tables():
        table = metadata.tables[name]
        pk_cols = {c.name for c in table.primary_key.columns}

        indexed_leading: set[str] = set()
        for idx in table.indexes:
            cols = list(idx.columns)
            if cols:
                indexed_leading.add(cols[0].name)
        for uc in table.constraints:
            if isinstance(uc, UniqueConstraint) and list(uc.columns):
                indexed_leading.add(next(iter(uc.columns)).name)

        for fk in table.foreign_keys:
            col = fk.parent.name
            if col in pk_cols or col in _AUDIT_AUTHOR_FK_COLUMNS:
                continue
            assert col in indexed_leading, (
                f"FK {name}.{col} → {fk.target_fullname} has no covering index. "
                "Add `index=True` to the column, declare an explicit Index, "
                "or include it as the leading column of a unique constraint."
            )


def test_no_varchar_columns() -> None:
    """data-model.md: all string columns are TEXT, not VARCHAR(N).

    Two narrow exceptions: the `prefix` and `secret_hash` columns on
    `id_token` and `id_session.session_hash` are fixed-length identifiers
    (visual prefix + hex sha256), not free-form text. `String(64)` /
    `String(32)` is honest about the shape and lets Postgres reject
    accidentally oversized writes. These columns are explicitly listed.
    """
    allowed_fixed_length = {
        ("id_token", "prefix"),
        ("id_token", "secret_hash"),
        ("id_session", "session_hash"),
    }
    for name in _all_business_tables():
        for col in metadata.tables[name].columns:
            type_str = str(col.type).upper()
            if type_str.startswith("VARCHAR"):
                assert (name, col.name) in allowed_fixed_length, (
                    f"{name}.{col.name} uses VARCHAR. Use Text() unless this "
                    "is a fixed-length identifier; if so, add it to the "
                    "allowed_fixed_length list in this test with rationale."
                )


def test_no_unknown_table_prefixes() -> None:
    for name in _all_business_tables():
        assert name.startswith(_ALLOWED_PREFIXES), (
            f"Table {name!r} uses an unknown prefix. Allowed: {_ALLOWED_PREFIXES}. "
            "If this is a new module prefix, update data-model.md and add it here."
        )


def test_trigger_table_list_matches_update_audit_tables() -> None:
    """Every table that has `updated_at` + `version` should be in
    TABLES_WITH_UPDATE_TRIGGER. Catches drift between the model file and
    the migration's trigger-attachment loop.
    """
    expected: set[str] = set()
    for name in _all_business_tables():
        if not name.startswith("id_"):
            continue
        cols = {c.name for c in metadata.tables[name].columns}
        if {"updated_at", "version"} <= cols:
            expected.add(name)
    assert set(_ALL_TABLES_WITH_UPDATE_TRIGGER) == expected, (
        "Update-trigger registry drift: "
        f"missing={expected - set(_ALL_TABLES_WITH_UPDATE_TRIGGER)}, "
        f"extra={set(_ALL_TABLES_WITH_UPDATE_TRIGGER) - expected}"
    )


def test_rls_table_list_includes_all_tenant_scoped_id_tables() -> None:
    """Every `id_*` table that carries `tenant_id` is in TABLES_WITH_RLS.
    `id_tenant` is excluded by design.
    """
    expected: set[str] = set()
    for name in _all_business_tables():
        if not name.startswith("id_"):
            continue
        if name in _GLOBAL_CATALOGUES:
            continue
        expected.add(name)
    assert set(_ALL_TABLES_WITH_RLS) == expected, (
        "RLS registry drift: "
        f"missing={expected - set(_ALL_TABLES_WITH_RLS)}, "
        f"extra={set(_ALL_TABLES_WITH_RLS) - expected}"
    )
