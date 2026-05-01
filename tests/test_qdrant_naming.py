"""Tests for the per-tenant Qdrant collection naming convention."""

from __future__ import annotations

import pytest

from openspine.core.qdrant import collection_name, parse_tenant_from_collection


@pytest.mark.parametrize(
    ("tenant_id", "expected"),
    [
        ("acme", "openspine__acme"),
        ("ACME", "openspine__acme"),
        ("550e8400-e29b-41d4-a716-446655440000", "openspine__550e8400-e29b-41d4-a716-446655440000"),
    ],
)
def test_collection_name_lowercases_and_prefixes(tenant_id: str, expected: str) -> None:
    assert collection_name(tenant_id) == expected


def test_parse_tenant_round_trip() -> None:
    name = collection_name("acme-eu")
    assert parse_tenant_from_collection(name) == "acme-eu"


def test_parse_tenant_returns_none_for_foreign_collection() -> None:
    assert parse_tenant_from_collection("not-ours-acme") is None
    assert parse_tenant_from_collection("") is None
