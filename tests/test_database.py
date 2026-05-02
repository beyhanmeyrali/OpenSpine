"""Tests for the SQLAlchemy declarative base + mixins.

These tests don't need a real database — they exercise the metadata,
naming convention, and mixin column declarations at the SQLAlchemy ORM
layer.
"""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Mapped, mapped_column

from openspine.core.database import (
    NAMING_CONVENTION,
    AuditMixin,
    Base,
    BusinessTableMixin,
    metadata,
)


def test_naming_convention_is_attached_to_metadata() -> None:
    assert metadata.naming_convention == NAMING_CONVENTION


def test_base_uses_shared_metadata() -> None:
    assert Base.metadata is metadata


def test_business_table_mixin_declares_id_tenant_audit_columns() -> None:
    class FakeBusinessTable(BusinessTableMixin, Base):
        __tablename__ = "fake_business"
        name: Mapped[str] = mapped_column()

    columns = {c.key for c in inspect(FakeBusinessTable).columns}
    assert {
        "id",
        "tenant_id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "version",
        "name",
    } <= columns


def test_audit_mixin_omits_tenant_id() -> None:
    class FakeGlobalCatalogue(AuditMixin, Base):
        __tablename__ = "fake_global"
        code: Mapped[str] = mapped_column()

    columns = {c.key for c in inspect(FakeGlobalCatalogue).columns}
    assert "tenant_id" not in columns
    assert {"id", "created_at", "created_by", "updated_at", "updated_by", "version"} <= columns


def test_business_table_id_is_primary_key() -> None:
    class FakeBusinessTablePK(BusinessTableMixin, Base):
        __tablename__ = "fake_business_pk"

    pk_cols = {c.key for c in inspect(FakeBusinessTablePK).primary_key}
    assert pk_cols == {"id"}


def test_business_table_version_default_is_one() -> None:
    class FakeBusinessTableVersion(BusinessTableMixin, Base):
        __tablename__ = "fake_business_version"

    version_col = inspect(FakeBusinessTableVersion).columns["version"]
    assert version_col.server_default is not None
    # SQLAlchemy stringifies the literal; "1" is what we set.
    assert "1" in str(version_col.server_default.arg)
